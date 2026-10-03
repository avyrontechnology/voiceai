"""MCP server envelope router — pilot (spec 0057).

The mirror of `adapters/mcp/client.py`: the client calls *remote* servers,
this router *serves* remote clients over Streamable HTTP (JSON-only in the
pilot; an SSE `Accept` still gets a JSON answer plus the session echo).
Dynamic by design: method names, error codes, page bounds, and the protocol
version live in `constants.py` or arrive through constructor callables — no
provider, tool, or tenant is special-cased here.

Pilot allowlist (loud, pinned in `CONTRACT.md`): `tools/call` serves the
tenant-visible non-deprecated non-`internal` rows (registry visibility), NOT
per-agent refs — per-agent binding lands with the `actions:` namespace in
S2. Unknown methods degrade to JSON-RPC `-32601` (never a transport 500),
and every failure carries identifiers + `error_id` only (never `str(exc)`).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C

__all__ = ["KeySnapshot", "McpServerRouter", "ServerToolHit"]

logger: logging.Logger = get_logger(C.MODULE_NAME)

_JSONRPC_VERSION: Final[str] = "2.0"
_METHOD_INITIALIZE: Final[str] = "initialize"
_METHOD_INITIALIZED: Final[str] = "notifications/initialized"
_METHOD_TOOLS_LIST: Final[str] = "tools/list"
_METHOD_TOOLS_CALL: Final[str] = "tools/call"
_ERROR_PARSE: Final[int] = -32700
_ERROR_INVALID_REQUEST: Final[int] = -32600
_ERROR_METHOD_NOT_FOUND: Final[int] = -32601
_ERROR_INVALID_PARAMS: Final[int] = -32602
_ERROR_INTERNAL: Final[int] = -32603
_CURSOR_KEY: Final[str] = "cursor"
_NAME_KEY: Final[str] = "name"
_ARGUMENTS_KEY: Final[str] = "arguments"
_TOOLS_KEY: Final[str] = "tools"
_NEXT_CURSOR_KEY: Final[str] = "nextCursor"
_SESSION_BYTES: Final[int] = 16

#: A row the pilot serves: descriptor shipped to clients + row behind it.
ServerToolHit = dict[str, Any]

#: `tools/call` handler seam: validated call in, JSON-able result out.
CallTool = Callable[[str, dict[str, Any]], Awaitable[Any]]
#: `tools/list` source seam: visible descriptors in server order.
ListTools = Callable[[], Awaitable[list[ServerToolHit]]]


def _error_envelope(code: int, message: str, envelope_id: Any) -> dict[str, Any]:
    """Build one JSON-RPC error response (identifiers only, never tracebacks).

    Args:
        code: JSON-RPC error code (`-32601` for unknown methods, …).
        message: Client-safe short reason (no payloads, no exception text).
        envelope_id: The request `id` echoed back (or `None` when unparseable).

    Returns:
        The error envelope (always `jsonrpc: "2.0"`).
    """
    return {"jsonrpc": _JSONRPC_VERSION, "id": envelope_id, "error": {"code": code, "message": message}}


class KeySnapshot:
    """Agent-snapshot stand-in for one external key (spec 0057 pilot).

    The pilot allowlist is registry visibility (NOT per-agent refs — pinned
    in `CONTRACT.md`): `tool_refs` carries the visible ids at call time, so
    the executor's deny path stays live even though every visible id passes.
    `agent_id` names the key principal for the audit row.
    """

    def __init__(self, agent_id: str, tool_refs: list[str]) -> None:
        """Bind the audit identity and its visible tool ids.

        Args:
            agent_id: Key-principal identity stamped on audit rows.
            tool_refs: Canonical tool ids this key may execute.
        """
        self._agent_id = agent_id
        self._tool_refs = tool_refs

    @property
    def agent_id(self) -> str:
        """The key-principal identity (audit only)."""
        return self._agent_id

    @property
    def tool_refs(self) -> list[str]:
        """The visible canonical tool ids."""
        return self._tool_refs


class McpServerRouter:
    """Dispatch JSON-RPC envelopes to the pilot tool lane.

    Args:
        list_tools: Visible tool descriptors (already tenant-filtered by the
            container-wired service — this router never reads the ambient
            tenant itself).
        call_tool: Validated execution seam (the container binds
            `ToolExecutor.execute_tool` with a key-allowlist snapshot).
        protocol_version: Negotiated version string echoed at `initialize`.
    """

    def __init__(
        self,
        *,
        list_tools: ListTools,
        call_tool: CallTool,
        protocol_version: str = C.MCP_PROTOCOL_VERSION,
    ) -> None:
        self._list_tools = list_tools
        self._call_tool = call_tool
        self._protocol_version = protocol_version

    def new_session_id(self) -> str:
        """Mint one opaque session id (`secrets`, never `random`).

        Returns:
            Hex session id for the `Mcp-Session-Id` response header.
        """
        return secrets.token_hex(_SESSION_BYTES)

    async def handle(self, envelope: Mapping[str, Any]) -> dict[str, Any] | None:
        """Dispatch one envelope to its method handler.

        Args:
            envelope: Decoded JSON-RPC object (`method` + optional `id`).

        Returns:
            The response envelope, or `None` for notifications (the
            controller answers those with a bare 202 + session echo).
        """
        if not isinstance(envelope, Mapping):
            return _error_envelope(_ERROR_INVALID_REQUEST, "Invalid JSON-RPC envelope.", None)
        envelope_id = envelope.get("id")
        method = envelope.get("method")
        params = envelope.get("params", {})
        if not isinstance(method, str) or not method:
            return _error_envelope(_ERROR_INVALID_REQUEST, "Missing method.", envelope_id)
        if not isinstance(params, Mapping):
            return _error_envelope(_ERROR_INVALID_PARAMS, "Params must be an object.", envelope_id)
        if method == _METHOD_INITIALIZE:
            return {
                "jsonrpc": _JSONRPC_VERSION,
                "id": envelope_id,
                "result": {
                    "protocolVersion": self._protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": C.MCP_SERVER_NAME, "version": self._protocol_version},
                },
            }
        if method == _METHOD_INITIALIZED:
            return None
        if method == _METHOD_TOOLS_LIST:
            return await self._handle_list(dict(params), envelope_id)
        if method == _METHOD_TOOLS_CALL:
            return await self._handle_call(dict(params), envelope_id)
        logger.warning("mcp server unknown method")
        return _error_envelope(_ERROR_METHOD_NOT_FOUND, f"Unknown method {method!r}.", envelope_id)

    async def _handle_list(self, params: dict[str, Any], envelope_id: Any) -> dict[str, Any]:
        """Serve one cursor page of tool descriptors (bounded, opaque cursor).

        Args:
            params: Optional `cursor` (opaque page offset issued here).
            envelope_id: The request `id` echoed back.

        Returns:
            `result.tools` plus `nextCursor` while pages remain.
        """
        try:
            offset = int(params.get(_CURSOR_KEY, 0) or 0)
        except (TypeError, ValueError):
            return _error_envelope(_ERROR_INVALID_PARAMS, "Invalid cursor.", envelope_id)
        if offset < 0:
            return _error_envelope(_ERROR_INVALID_PARAMS, "Invalid cursor.", envelope_id)
        descriptors = await self._list_tools()
        page_size = min(C.MCP_LIST_PAGE_SIZE, C.MCP_LIST_PAGE_MAX)
        page = descriptors[offset : offset + page_size]
        result: dict[str, Any] = {_TOOLS_KEY: page}
        if offset + page_size < len(descriptors):
            result[_NEXT_CURSOR_KEY] = str(offset + page_size)
        return {"jsonrpc": _JSONRPC_VERSION, "id": envelope_id, "result": result}

    async def _handle_call(self, params: dict[str, Any], envelope_id: Any) -> dict[str, Any]:
        """Execute one named tool through the injected seam (identifiers only on failure).

        Args:
            params: `name` + `arguments` (both validated, never echoed raw).
            envelope_id: The request `id` echoed back.

        Returns:
            `result` on success, JSON-RPC `error` on refusal/failure.
        """
        name = params.get(_NAME_KEY)
        arguments = params.get(_ARGUMENTS_KEY, {})
        if not isinstance(name, str) or not name:
            return _error_envelope(_ERROR_INVALID_PARAMS, "Missing tool name.", envelope_id)
        if not isinstance(arguments, dict):
            return _error_envelope(_ERROR_INVALID_PARAMS, "Arguments must be an object.", envelope_id)
        try:
            outcome = await self._call_tool(name, arguments)
        except Exception as exc:  # seam refusals are values here, never tracebacks
            logger.warning("mcp server tool call refused (%s)", type(exc).__name__)
            return _error_envelope(_ERROR_INTERNAL, "Tool execution failed.", envelope_id)
        return {"jsonrpc": _JSONRPC_VERSION, "id": envelope_id, "result": outcome}
