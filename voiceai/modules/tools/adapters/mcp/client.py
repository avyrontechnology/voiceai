"""MCP HTTP+SSE JSON-RPC client (spec 0056, Phase-3).

Dynamic by design: no provider URL, method name, or version is hard-coded at
call sites — everything flows from `IntegrationTemplate.client_config`,
`PartnerIntegration.negotiated_protocol_version`, and `constants.py`. The
executor owns routing; this client owns the wire: `initialize`/`initialized`
handshake, cursor-paginated `tools/list`, and `tools/call` over JSON or
`text/event-stream` (SSE) transports via a shared `httpx.AsyncClient` seam.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Final

import httpx

from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C

__all__ = ["McpHttpClient", "McpTool", "parse_sse_events"]

logger = get_logger(C.MODULE_NAME)

_JSONRPC_VERSION: Final[str] = "2.0"
_METHOD_INITIALIZE: Final[str] = "initialize"
_METHOD_INITIALIZED: Final[str] = "notifications/initialized"
_METHOD_TOOLS_LIST: Final[str] = "tools/list"
_METHOD_TOOLS_CALL: Final[str] = "tools/call"
_SSE_CONTENT_TYPE: Final[str] = "text/event-stream"
_SESSION_ID_HEADER: Final[str] = "Mcp-Session-Id"
_CURSOR_KEY: Final[str] = "cursor"
_NEXT_CURSOR_KEY: Final[str] = "nextCursor"
_TOOLS_KEY: Final[str] = "tools"
_RESULT_KEY: Final[str] = "result"
_ERROR_KEY: Final[str] = "error"
_DATA_PREFIX: Final[str] = "data:"
_DEFAULT_TIMEOUT_S: Final[float] = 30.0
_DEFAULT_PAGE_LIMIT: Final[int] = 50


class McpTool(dict):  # why: remote tool shapes are free-form JSON Schema
    """One remote tool descriptor (`name`, `description`, `inputSchema`)."""


def parse_sse_events(body: str) -> list[Any]:
    """Parse an SSE body into JSON payloads (pure, deterministic).

    Args:
        body: Raw `text/event-stream` text (`data:` lines, `:` comments ignored).

    Returns:
        Decoded JSON payloads in stream order (non-JSON lines skipped).
    """
    payloads: list[Any] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith(_DATA_PREFIX):
            continue
        raw = stripped[len(_DATA_PREFIX) :].strip()
        if not raw:
            continue
        try:
            payloads.append(json.loads(raw))
        except ValueError:
            continue
    return payloads


class McpHttpClient:
    """JSON-RPC over HTTP for one MCP server session.

    Args:
        base_url: Server base URL (already SSRF-checked by the caller/DCR).
        access_token: Bearer token, or `None` for public servers.
        timeout_s: Per-request timeout budget.
        client_factory: `httpx.AsyncClient` seam (tests inject `MockTransport`).
    """

    def __init__(
        self,
        base_url: str,
        access_token: str | None = None,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._timeout_s = timeout_s
        self._client_factory = client_factory
        self._session_id: str | None = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build request headers (bearer + session resume, never secrets in logs).

        Args:
            extra: Additional headers (content-type set by the caller).

        Returns:
            The header mapping for one request.
        """
        headers: dict[str, str] = dict(extra or {})
        if self._access_token:
            headers[C.AUTH_AUTHORIZATION_HEADER] = f"{C.AUTH_BEARER_SCHEME} {self._access_token}"
        if self._session_id:
            headers[_SESSION_ID_HEADER] = self._session_id
        return headers

    def _client(self) -> httpx.AsyncClient:
        """Build one bounded client (factory seam first, redirect-pinned default).

        Returns:
            A configured async HTTP client (caller closes via context).
        """
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=False)

    async def _post(self, payload: dict[str, Any]) -> Any:
        """POST one JSON-RPC envelope and decode JSON or SSE responses.

        Args:
            payload: The JSON-RPC envelope (`jsonrpc`, `id`, `method`, `params`).

        Returns:
            The decoded `result` (or SSE payload list for streams).

        Raises:
            ToolExecutionError: On transport failure or JSON-RPC `error`.
        """
        from voiceai.modules.tools.errors import ToolExecutionError

        try:
            async with self._client() as client:
                response = await client.post(
                    self._base_url,
                    json=payload,
                    headers=self._headers({"Content-Type": "application/json"}),
                )
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                "MCP server unreachable.",
                details={"method": str(payload.get("method", ""))},
                cause=exc,
            ) from exc
        session = response.headers.get(_SESSION_ID_HEADER)
        if session:
            self._session_id = session
        content_type = response.headers.get("content-type", "")
        if _SSE_CONTENT_TYPE in content_type:
            return parse_sse_events(response.text)
        try:
            envelope = response.json()
        except ValueError as exc:
            raise ToolExecutionError(
                "MCP server returned a non-JSON response.",
                details={"method": str(payload.get("method", ""))},
                cause=exc,
            ) from exc
        if isinstance(envelope, dict) and _ERROR_KEY in envelope:
            raise ToolExecutionError(
                "MCP server refused the request.",
                details={"method": str(payload.get("method", ""))},
            )
        if isinstance(envelope, dict):
            return envelope.get(_RESULT_KEY, envelope)
        return envelope

    async def initialize(self, protocol_version: str) -> dict[str, Any]:
        """Run the MCP `initialize` + `initialized` handshake.

        Args:
            protocol_version: Client protocol version to negotiate.

        Returns:
            Server capabilities (`result` of `initialize`).
        """
        result = await self._post(
            {
                "jsonrpc": _JSONRPC_VERSION,
                "id": 1,
                "method": _METHOD_INITIALIZE,
                "params": {"protocolVersion": protocol_version, "capabilities": {}},
            }
        )
        try:
            async with self._client() as client:
                await client.post(
                    self._base_url,
                    json={"jsonrpc": _JSONRPC_VERSION, "method": _METHOD_INITIALIZED},
                    headers=self._headers({"Content-Type": "application/json"}),
                )
        except httpx.HTTPError:
            logger.warning("mcp initialized notification failed")
        return result if isinstance(result, dict) else {}

    async def list_tools(self, *, cursor: str | None = None) -> tuple[list[McpTool], str | None]:
        """List one page of remote tools.

        Args:
            cursor: Opaque pagination cursor, or `None` for the first page.

        Returns:
            `(tools, next_cursor)` — empty cursor ends pagination.
        """
        params: dict[str, Any] = {}
        if cursor:
            params[_CURSOR_KEY] = cursor
        result = await self._post(
            {"jsonrpc": _JSONRPC_VERSION, "id": 2, "method": _METHOD_TOOLS_LIST, "params": params}
        )
        if not isinstance(result, dict):
            return [], None
        tools = [McpTool(item) for item in result.get(_TOOLS_KEY, []) if isinstance(item, dict)]
        nxt = result.get(_NEXT_CURSOR_KEY)
        return tools, str(nxt) if nxt else None

    async def list_all_tools(self, *, limit: int = _DEFAULT_PAGE_LIMIT) -> list[McpTool]:
        """List every remote tool, following cursor pagination (bounded).

        Args:
            limit: Max pages to follow (runaway-server guard).

        Returns:
            All tool descriptors in server order.
        """
        collected: list[McpTool] = []
        cursor: str | None = None
        for _ in range(max(1, limit)):
            page, cursor = await self.list_tools(cursor=cursor)
            collected.extend(page)
            if not cursor:
                break
        return collected

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one remote tool by name.

        Args:
            name: Remote (un-namespaced) tool name.
            arguments: Validated call arguments.

        Returns:
            The decoded `result` (JSON value or SSE payload list).
        """
        return await self._post(
            {
                "jsonrpc": _JSONRPC_VERSION,
                "id": 3,
                "method": _METHOD_TOOLS_CALL,
                "params": {"name": name, "arguments": arguments},
            }
        )
