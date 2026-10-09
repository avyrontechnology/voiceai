"""Tenant-scoped tool execution with audit logging (spec 0056, Phase-1).

Internal-only entry point: chat (`complete_chat_turn` tool_calls) and voice
(`execute_function_call`) call `ToolExecutor.execute_tool` through the
container-injected `ToolExecutorPort` (`voice/adapters/tools_port.py`) — there
is deliberately no public `POST /tools/{id}/execute` route in Phase-1.

Pipeline per call: strip the MCP namespace → allowlist the id against the
agent snapshot's `tool_refs` → resolve the row → validate arguments against
the row's JSON-Schema contract → SSRF pre-flight off the event loop →
route internal (no endpoint) / HTTP (optional credential headers) → on 401
invalidate + force-refresh + exactly one retry → audit-log fail-open.
Refusals (deny/schema/SSRF/unknown) raise `AppError`s with identifiers only;
upstream failures (timeout/unreachable/non-2xx) return `success=False`
results carrying an `error_id` that stitches the caller to the log line.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from voiceai.common.constants import ERROR_ID_LENGTH, HTTP_UNAUTHORIZED, MILLISECONDS_PER_SECOND
from voiceai.common.errors import AppError
from voiceai.common.logger import get_logger
from voiceai.common.security import is_safe_outbound_url, redact_secrets
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.adapters.mcp.client import McpHttpClient
from voiceai.modules.tools.auth_resolver import AuthParams, ToolAuthResolver
from voiceai.modules.tools.errors import ToolExecutionError
from voiceai.modules.tools.exceptions import (
    ensure_allowlisted,
    ensure_arguments_valid,
    ensure_endpoint_safe,
    ensure_tool_executable,
)
from voiceai.modules.tools.models import ToolDefinition, ToolExecutionLog
from voiceai.modules.tools.repository import LogsRepository, ToolsRepository
from voiceai.modules.tools.static_methods import (
    fingerprint_tool_call,
    strip_mcp_prefix,
    validate_arguments,
)

__all__ = ["AgentSnapshot", "HttpClientFactory", "ToolExecutionResult", "ToolExecutor"]

logger: logging.Logger = get_logger(C.MODULE_NAME)

#: Factory building one bounded HTTP client per execution (tests inject a
#: `MockTransport` client; production uses redirect-pinned `httpx` defaults).
HttpClientFactory = Callable[[float], httpx.AsyncClient]


class AgentSnapshot(Protocol):
    """Structural agent execution snapshot (spec 0056, Phase-1).

    A Protocol (not an agents import) so tools never depend on a sibling
    module's internals: chat/voice adapt their dumps to this shape.
    """

    @property
    def agent_id(self) -> str:
        """The owning agent id (the allowlist principal)."""
        ...

    @property
    def tool_refs(self) -> Sequence[str]:
        """The attached canonical tool ids the agent may execute."""
        ...


class ToolExecutionResult(BaseModel):
    """Ephemeral outcome of one guarded tool call (never persisted as-is).

    Attributes:
        tool_id: Canonical executed id (MCP namespace stripped).
        agent_id: Owning agent.
        success: Whether the call answered successfully.
        result_summary: Short safe line (status/timings only, never secrets).
        data: Upstream payload for the caller (in-memory only — audit rows
            carry the summary, never this).
        call_fingerprint: Stable idempotency fingerprint of id + arguments.
        execution_ms: Guarded wall time.
        error_id: Log-stitching id for failures.
    """

    tool_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    success: bool = False
    result_summary: str = ""
    data: dict = Field(default_factory=dict)  # why: upstream tool payloads are free-form JSON
    call_fingerprint: str = ""
    execution_ms: int = Field(default=0, ge=0)
    error_id: str | None = None


def _default_client_factory(timeout_s: float) -> httpx.AsyncClient:
    """Build a redirect-pinned client bounded by `timeout_s`.

    Args:
        timeout_s: Per-call timeout budget (already clamped to the row range).

    Returns:
        The client (one execution owns it via `async with`).
    """
    return httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)


def _elapsed_ms(started: float) -> int:
    """Return whole milliseconds since a `perf_counter` reading.

    Args:
        started: The `perf_counter` value taken when execution began.

    Returns:
        Elapsed milliseconds, never negative.
    """
    return max(0, int((time.perf_counter() - started) * MILLISECONDS_PER_SECOND))


def _clamp_timeout(timeout_s: int) -> float:
    """Clamp a row timeout into the accepted budget range.

    Args:
        timeout_s: The row's per-call budget.

    Returns:
        Seconds within `[TOOL_TIMEOUT_MIN_S, TOOL_TIMEOUT_MAX_S]`.
    """
    return float(max(C.TOOL_TIMEOUT_MIN_S, min(timeout_s, C.TOOL_TIMEOUT_MAX_S)))


class ToolExecutor:
    """Guarded tenant-scoped tool runner with audit logging (spec 0056, Phase-1).

    Args:
        tools: The tool registry view (system + ambient tenant, container-wired).
        logs: The execution-log view (fail-open — audit loss never fails calls).
        auth: The credential header resolver (cache-first, store fallback).
        http_client_factory: Client seam (None means redirect-pinned `httpx`
            with the row timeout; tests inject `MockTransport` clients).
        url_guard: SSRF verdict seam (defaults to `is_safe_outbound_url`;
            always runs off the event loop via `to_thread`).
    """

    def __init__(
        self,
        tools: ToolsRepository,
        logs: LogsRepository,
        auth: ToolAuthResolver,
        *,
        http_client_factory: HttpClientFactory | None = None,
        url_guard: Callable[[str], bool] = is_safe_outbound_url,
    ) -> None:
        self._tools = tools
        self._logs = logs
        self._auth = auth
        self._http_client_factory = http_client_factory
        self._url_guard = url_guard

    async def execute_tool(
        self,
        *,
        tool_id: str,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
        agent_snapshot: AgentSnapshot,
        tenant: str,
    ) -> ToolExecutionResult:
        """Execute one tool call for an agent, guarded and audit-logged.

        Args:
            tool_id: Possibly MCP-namespaced id (stripped before the allowlist).
            arguments: Call arguments (validated, then redacted for the log).
            agent_snapshot: Owning agent (allowlist principal + attached refs).
            tenant: Acting tenant (stamped on the audit row).

        Returns:
            The outcome (upstream failures arrive as `success=False`, never
            as transport text — see the module docstring).

        Raises:
            ToolDeniedError: When the id is outside the agent's refs (403).
            ToolExecutionError: On schema/SSRF/credential refusals (400).
            ToolNotFoundError: When no visible row exists (404).
        """
        started = time.perf_counter()
        canonical = strip_mcp_prefix(tool_id)
        agent_id = agent_snapshot.agent_id
        redacted = redact_secrets(arguments)
        try:
            result = await self._execute_checked(canonical, arguments, agent_snapshot, started)
        except AppError as exc:
            refused = ToolExecutionResult(
                tool_id=canonical,
                agent_id=agent_id,
                success=False,
                result_summary=exc.public_message[: C.EXECUTOR_SUMMARY_MAX_CHARS],
                execution_ms=_elapsed_ms(started),
                error_id=exc.error_id,
            )
            logger.warning(C.LOG_TOOL_REFUSED, canonical, exc.error_id)
            await self._record(refused, redacted, tenant)
            raise
        await self._record(result, redacted, tenant)
        return result

    async def _execute_checked(
        self,
        canonical: str,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
        agent_snapshot: AgentSnapshot,
        started: float,
    ) -> ToolExecutionResult:
        """Run the guard chain and route to the internal or HTTP path.

        Args:
            canonical: Stripped tool id.
            arguments: Raw call arguments.
            agent_snapshot: Owning agent.
            started: The `perf_counter` value taken by `execute_tool`.

        Returns:
            The outcome.

        Raises:
            ToolDeniedError: On allowlist misses.
            ToolNotFoundError: On unknown ids.
            ToolExecutionError: On schema or pre-flight refusals.
        """
        ensure_allowlisted(canonical, agent_snapshot.agent_id, agent_snapshot.tool_refs)
        tool = ensure_tool_executable(await self._tools.get_tool(canonical), canonical)
        ensure_arguments_valid(validate_arguments(tool.parameters, arguments), canonical)
        fingerprint = fingerprint_tool_call(canonical, arguments)
        if tool.url is None or tool.kind == C.TOOL_KIND_INTERNAL:
            return self._execute_internal(tool, agent_snapshot.agent_id, arguments, fingerprint, started)
        if isinstance(tool.meta, dict) and tool.meta.get("transport") == C.TRANSPORT_MCP:
            return await self._execute_mcp(tool, agent_snapshot.agent_id, arguments, fingerprint, started)
        return await self._execute_http(tool, agent_snapshot.agent_id, arguments, fingerprint, started)

    def _execute_internal(
        self,
        tool: ToolDefinition,
        agent_id: str,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
        fingerprint: str,
        started: float,
    ) -> ToolExecutionResult:
        """Answer an endpoint-less (internal) tool without any network call.

        Args:
            tool: The resolved row.
            agent_id: Owning agent.
            arguments: Raw call arguments (redacted into the payload echo).
            fingerprint: The call fingerprint.
            started: The `perf_counter` value taken by `execute_tool`.

        Returns:
            A successful result echoing redacted arguments (behavior tools
            gain real dispatch in a later phase — never raw secrets).
        """
        elapsed = _elapsed_ms(started)
        return ToolExecutionResult(
            tool_id=tool.tool_id,
            agent_id=agent_id,
            success=True,
            result_summary=C.EXECUTOR_INTERNAL_SUMMARY_TEMPLATE.format(name=tool.name, ms=elapsed),
            data={
                C.EXECUTOR_DATA_TOOL: tool.name,
                C.EXECUTOR_DATA_ARGUMENTS: redact_secrets(arguments),
                C.EXECUTOR_DATA_FINGERPRINT: fingerprint,
            },
            call_fingerprint=fingerprint,
            execution_ms=elapsed,
            error_id=None,
        )

    async def _execute_http(
        self,
        tool: ToolDefinition,
        agent_id: str,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
        fingerprint: str,
        started: float,
    ) -> ToolExecutionResult:
        """Run the SSRF pre-flight and the HTTP call with exactly-once 401 retry.

        Args:
            tool: The resolved row (endpoint + optional credential pointer).
            agent_id: Owning agent.
            arguments: Raw call arguments.
            fingerprint: The call fingerprint.
            started: The `perf_counter` value taken by `execute_tool`.

        Returns:
            The outcome (transport failures arrive as `success=False`).

        Raises:
            ToolExecutionError: When the endpoint fails the SSRF pre-flight
                or the credential pointer is unresolvable.
        """
        url = tool.url or ""
        safe = await asyncio.to_thread(self._url_guard, url)
        ensure_endpoint_safe(safe, tool.tool_id)
        timeout_s = _clamp_timeout(tool.timeout_s)
        auth = await self._auth.resolve_auth(tool)
        factory = self._http_client_factory or _default_client_factory
        try:
            async with factory(timeout_s) as client:
                response = await self._call_with_auth_retry(client, tool, url, auth, arguments)
        except httpx.TimeoutException:
            return self._timeout_result(tool, agent_id, fingerprint, started, timeout_s)
        except httpx.HTTPError as exc:
            return self._unreachable_result(tool, agent_id, fingerprint, started, exc)
        return self._result_from_response(tool, agent_id, fingerprint, started, response)

    async def _execute_mcp(
        self,
        tool: ToolDefinition,
        agent_id: str,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
        fingerprint: str,
        started: float,
    ) -> ToolExecutionResult:
        """Run one MCP-protocol call: handshake then `tools/call` (spec 0060).

        Query-placed keys cannot ride MCP (the spec has no query auth), so
        they refuse here with guidance instead of silently dropping the key.
        Refresh semantics mirror HTTP: one invalidate + force-refresh + retry
        on failure, then the failure value (never transport text).

        Args:
            tool: The synced row (`meta.transport == "mcp"`, `meta.mcp_tool`
                names the remote tool, `url` is the server endpoint).
            agent_id: Owning agent.
            arguments: Raw call arguments (validated upstream).
            fingerprint: The call fingerprint.
            started: The `perf_counter` value taken by `execute_tool`.

        Returns:
            The outcome (failures arrive as `success=False` values).

        Raises:
            ToolExecutionError: When query-placed auth or an unresolvable
                credential meets this path (fail-closed refusals).
        """
        url = tool.url or ""
        safe = await asyncio.to_thread(self._url_guard, url)
        ensure_endpoint_safe(safe, tool.tool_id)
        timeout_s = _clamp_timeout(tool.timeout_s)
        auth = await self._auth.resolve_auth(tool)
        if auth.query:
            raise ToolExecutionError(
                "Query-placed keys cannot ride MCP calls — use header mode.",
                details={"tool_id": tool.tool_id},
            )
        remote = tool.meta.get("mcp_tool", tool.name) if isinstance(tool.meta, dict) else tool.name
        if not isinstance(remote, str) or not remote:
            remote = tool.name
        last_exc: ToolExecutionError | None = None
        for _ in range(C.RETRY_ONCE + 1):
            try:
                client = McpHttpClient(url, timeout_s=timeout_s, extra_headers=dict(auth.headers))
                await client.initialize(C.MCP_PROTOCOL_VERSION)
                outcome = await client.call_tool(remote, dict(arguments))
                return ToolExecutionResult(
                    tool_id=tool.tool_id,
                    agent_id=agent_id,
                    success=True,
                    result_summary=C.EXECUTOR_MCP_SUMMARY_TEMPLATE.format(tool=tool.name, ms=_elapsed_ms(started)),
                    data=outcome
                    if isinstance(outcome, dict)
                    else {C.EXECUTOR_TEXT_KEY: str(outcome)[: C.EXECUTOR_RESPONSE_MAX_CHARS]},
                    call_fingerprint=fingerprint,
                    execution_ms=_elapsed_ms(started),
                    error_id=None,
                )
            except ToolExecutionError as exc:
                last_exc = exc
                if tool.auth_ref is None:
                    break
                await self._auth.invalidate_credential_cache(tool.auth_ref)
                try:
                    auth = await self._auth.resolve_auth(tool, force_refresh=True)
                except ToolExecutionError:
                    break
                if auth.query:
                    raise ToolExecutionError(
                        "Query-placed keys cannot ride MCP calls — use header mode.",
                        details={"tool_id": tool.tool_id},
                    ) from last_exc
        if last_exc is None:  # defensive — the loop always runs at least once
            last_exc = ToolExecutionError("Tool execution produced no attempts.")
        return self._unreachable_result(tool, agent_id, fingerprint, started, last_exc)

    async def _call_with_auth_retry(
        self,
        client: httpx.AsyncClient,
        tool: ToolDefinition,
        url: str,
        auth: AuthParams,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
    ) -> httpx.Response:
        """Call once, retrying exactly once after refresh on a credentialed 401.

        Args:
            client: The bounded execution client.
            tool: The resolved row.
            url: The pre-flighted endpoint.
            auth: The resolved headers + query halves.
            arguments: Raw call arguments.

        Returns:
            The final response (a repeated 401 answers as-is — single retry).
        """
        response = await self._call_once(client, tool, url, auth, arguments)
        for _ in range(C.RETRY_ONCE):
            if response.status_code != HTTP_UNAUTHORIZED or tool.auth_ref is None:
                break
            await self._auth.invalidate_credential_cache(tool.auth_ref)
            auth = await self._auth.resolve_auth(tool, force_refresh=True)
            response = await self._call_once(client, tool, url, auth, arguments)
        return response

    async def _call_once(
        self,
        client: httpx.AsyncClient,
        tool: ToolDefinition,
        url: str,
        auth: AuthParams,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
    ) -> httpx.Response:
        """Fire one bounded attempt (GET carries params, the rest carry JSON).

        Query-param credentials merge into the URL params on every method
        (arguments still ride params on GET, JSON body otherwise).

        Args:
            client: The bounded execution client.
            tool: The resolved row (only `method` is read).
            url: The pre-flighted endpoint.
            auth: The resolved headers + query halves.
            arguments: Raw call arguments.

        Returns:
            The upstream response.
        """
        method = (tool.method or C.DEFAULT_TOOL_METHOD).upper()
        headers = auth.headers
        if method == C.EXECUTOR_GET_METHOD:
            return await client.get(url, headers=headers, params={**auth.query, **dict(arguments)})
        return await client.request(method, url, headers=headers, params=dict(auth.query), json=dict(arguments))

    def _result_from_response(
        self,
        tool: ToolDefinition,
        agent_id: str,
        fingerprint: str,
        started: float,
        response: httpx.Response,
    ) -> ToolExecutionResult:
        """Render an upstream response as a result (bodies never reach summaries).

        Args:
            tool: The resolved row.
            agent_id: Owning agent.
            fingerprint: The call fingerprint.
            started: The `perf_counter` value taken by `execute_tool`.
            response: The final upstream response.

        Returns:
            Success on 2xx, failure otherwise (with an `error_id` for the log).
        """
        elapsed = _elapsed_ms(started)
        success = httpx.codes.is_success(response.status_code)
        return ToolExecutionResult(
            tool_id=tool.tool_id,
            agent_id=agent_id,
            success=success,
            result_summary=C.EXECUTOR_HTTP_SUMMARY_TEMPLATE.format(status=response.status_code, ms=elapsed),
            data=self._response_data(response),
            call_fingerprint=fingerprint,
            execution_ms=elapsed,
            error_id=None if success else uuid4().hex[:ERROR_ID_LENGTH],
        )

    def _timeout_result(
        self,
        tool: ToolDefinition,
        agent_id: str,
        fingerprint: str,
        started: float,
        timeout_s: float,
    ) -> ToolExecutionResult:
        """Render a hung upstream as a failure value (never transport text).

        Args:
            tool: The resolved row.
            agent_id: Owning agent.
            fingerprint: The call fingerprint.
            started: The `perf_counter` value taken by `execute_tool`.
            timeout_s: The budget that was exceeded.

        Returns:
            A `success=False` result stitched to the warning log by `error_id`.
        """
        error_id = uuid4().hex[:ERROR_ID_LENGTH]
        logger.warning(C.LOG_TOOL_TIMED_OUT, tool.tool_id, error_id)
        return ToolExecutionResult(
            tool_id=tool.tool_id,
            agent_id=agent_id,
            success=False,
            result_summary=C.EXECUTOR_TIMEOUT_SUMMARY_TEMPLATE.format(timeout=timeout_s),
            call_fingerprint=fingerprint,
            execution_ms=_elapsed_ms(started),
            error_id=error_id,
        )

    def _unreachable_result(
        self,
        tool: ToolDefinition,
        agent_id: str,
        fingerprint: str,
        started: float,
        exc: Exception,
    ) -> ToolExecutionResult:
        """Render a transport failure as a failure value (type logged, text dropped).

        Args:
            tool: The resolved row.
            agent_id: Owning agent.
            fingerprint: The call fingerprint.
            started: The `perf_counter` value taken by `execute_tool`.
            exc: The transport failure (its type is logged; its text — which
                can echo addresses or protocol errors — never reaches the caller).

        Returns:
            A `success=False` result stitched to the warning log by `error_id`.
        """
        error_id = uuid4().hex[:ERROR_ID_LENGTH]
        logger.warning(C.LOG_TOOL_UNREACHABLE, tool.tool_id, type(exc).__name__)
        return ToolExecutionResult(
            tool_id=tool.tool_id,
            agent_id=agent_id,
            success=False,
            result_summary=C.EXECUTOR_UNREACHABLE_SUMMARY,
            call_fingerprint=fingerprint,
            execution_ms=_elapsed_ms(started),
            error_id=error_id,
        )

    @staticmethod
    def _response_data(response: httpx.Response) -> dict[str, Any]:  # why: upstream payloads are free-form JSON
        """Extract the caller-facing payload without ever trusting its shape.

        Args:
            response: The final upstream response.

        Returns:
            The JSON mapping, or a bounded `{"text": ...}` fallback for
            non-JSON or non-mapping bodies.
        """
        try:
            payload = response.json()
        except ValueError:
            return {C.EXECUTOR_TEXT_KEY: response.text[: C.EXECUTOR_RESPONSE_MAX_CHARS]}
        if isinstance(payload, Mapping):
            return {str(key): value for key, value in payload.items()}
        return {C.EXECUTOR_TEXT_KEY: str(payload)[: C.EXECUTOR_RESPONSE_MAX_CHARS]}

    async def _record(
        self,
        result: ToolExecutionResult,
        arguments: Mapping[str, Any],  # why: redacted arguments are free-form JSON
        tenant: str,
    ) -> None:
        """Persist the audit row (already redacted) and emit the operator line.

        The repository is fail-open, so this never raises: logging the call
        must not break the call.

        Args:
            result: The outcome to audit.
            arguments: The redacted arguments.
            tenant: Acting tenant (stamped on the row).
        """
        log = ToolExecutionLog(
            tool_id=result.tool_id,
            agent_id=result.agent_id,
            arguments=dict(arguments),
            result_summary=result.result_summary[: C.EXECUTOR_SUMMARY_MAX_CHARS],
            success=result.success,
            execution_ms=result.execution_ms,
            error_id=result.error_id,
            tenant_id=tenant,
        )
        await self._logs.save_log(log)
        logger.info(C.LOG_TOOL_EXECUTED, result.tool_id, result.success, result.execution_ms)
