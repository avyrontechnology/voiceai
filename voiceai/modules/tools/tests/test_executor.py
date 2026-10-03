"""Phase-1 executor + auth-resolver behavior over fakes (spec 0056).

Offline only: HTTP rides `httpx.MockTransport`, DNS rides an injected guard,
Redis/credential stores are in-memory fakes. Every execution path is pinned:
schema-invalid, allowlist-deny, SSRF-block, 401-then-success (exactly-once
refresh), 401-then-401 (single retry only), timeout, unreachable, internal
(no HTTP), and log fail-open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.auth_resolver import ToolAuthResolver
from voiceai.modules.tools.constants import ToolKind
from voiceai.modules.tools.errors import ToolDeniedError, ToolExecutionError, ToolNotFoundError
from voiceai.modules.tools.executor import ToolExecutor
from voiceai.modules.tools.models import ToolDefinition, ToolExecutionLog
from voiceai.modules.tools.repository import LogsRepository, ToolsRepository
from voiceai.modules.tools.static_methods import (
    fingerprint_tool_call,
    strip_mcp_prefix,
    validate_arguments,
)

TENANT = "acme"
TOOL_ID = "function:book"
AGENT_ID = "agent-1"
SECRET = "s3cr3t-value"


@dataclass
class _Snapshot:
    """Agent-snapshot fake satisfying the executor's structural Protocol."""

    agent_id: str = AGENT_ID
    tool_refs: list[str] = field(default_factory=list)


class _Cache:
    """CachePort fake recording every op (fail mode simulates a dead Redis)."""

    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.values: dict[str, str] = dict(values or {})
        self.gets: list[str] = []
        self.deletes: list[str] = []
        self.fail = fail

    async def get(self, key: str) -> str | None:
        """Record the read, then answer from the map (or raise in fail mode)."""
        self.gets.append(key)
        if self.fail:
            raise RuntimeError("redis down")
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Record the write (or raise in fail mode)."""
        if self.fail:
            raise RuntimeError("redis down")
        self.values[key] = value

    async def delete(self, key: str) -> None:
        """Record the invalidation (or raise in fail mode)."""
        self.deletes.append(key)
        if self.fail:
            raise RuntimeError("redis down")
        self.values.pop(key, None)


class _Store:
    """CredentialStorePort fake scripting one envelope per fetch."""

    def __init__(
        self, script: list[dict[str, Any] | None] | None = None
    ) -> None:  # why: vault envelopes are free-form JSON
        self._script: list[dict[str, Any] | None] = list(script or [])  # why: see `script` argument
        self.fetches = 0

    async def fetch_credential(self, auth_ref: str) -> dict[str, Any] | None:  # why: vault envelopes are free-form JSON
        """Pop the next scripted envelope (repeating the last when exhausted)."""
        del auth_ref
        self.fetches += 1
        if not self._script:
            return None
        if len(self._script) > 1:
            return self._script.pop(0)
        return self._script[0]


class _Transport:
    """MockTransport handler scripting responses (or raising transport errors)."""

    def __init__(self, script: list[httpx.Response | Exception]) -> None:
        self._script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request, then answer or raise per the script."""
        self.requests.append(request)
        item = self._script.pop(0) if self._script else httpx.Response(500)
        if isinstance(item, Exception):
            raise item
        return item


class _FailingLogs(InMemoryRepository[ToolExecutionLog]):
    """Log store whose writes always fail (fail-open pin)."""

    async def insert(self, model: ToolExecutionLog) -> ToolExecutionLog:
        """Raise instead of persisting."""
        raise RuntimeError("disk on fire")


def _row(
    tool_id: str = TOOL_ID,
    *,
    url: str | None = "https://api.test/book",
    auth_ref: str | None = None,
    parameters: dict[str, Any] | None = None,  # why: JSON-Schema contracts are free-form JSON
    method: str = "POST",
    kind: ToolKind = "function",
    name: str = "book",
) -> ToolDefinition:
    """One tool row (id pinned by the repository)."""
    return ToolDefinition(
        tool_id=tool_id,
        kind=kind,
        name=name,
        description="booking tool",
        parameters=dict(parameters or {}),
        url=url,
        method=method,
        auth_ref=auth_ref,
    )


async def _harness(
    rows: list[ToolDefinition],
    *,
    script: list[httpx.Response | Exception] | None = None,
    guard: bool = True,
    cache: _Cache | None = None,
    store: _Store | None = None,
    failing_logs: bool = False,
) -> tuple[ToolExecutor, _Transport, _Cache, _Store, InMemoryRepository[ToolExecutionLog]]:
    """Executor over throwaway stores with a scripted transport.

    Returns:
        The executor, transport spy, cache fake, store fake, and raw log
        store (for audit assertions).
    """
    transport = _Transport(script or [])
    cache_fake = cache or _Cache()
    store_fake = store if store is not None else _Store()
    db = InMemoryDatabase()
    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    for row in rows:
        await tools.save_tool(row)
    raw_logs: InMemoryRepository[ToolExecutionLog] = (
        _FailingLogs(db, Collections.TOOL_EXECUTION_LOGS, ToolExecutionLog)
        if failing_logs
        else InMemoryRepository[ToolExecutionLog](db, Collections.TOOL_EXECUTION_LOGS, ToolExecutionLog)
    )
    logs = LogsRepository(
        TenantScopedRepository(raw_logs, TENANT, Collections.TOOL_EXECUTION_LOGS),
    )
    auth = ToolAuthResolver(redis_cache=cache_fake, credential_store=store_fake)

    def factory(timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=timeout_s)

    executor = ToolExecutor(
        tools,
        logs,
        auth,
        http_client_factory=factory,
        url_guard=lambda url: guard,
    )
    return executor, transport, cache_fake, store_fake, raw_logs


async def _log_rows(raw: InMemoryRepository[ToolExecutionLog]) -> list[ToolExecutionLog]:
    """Every audit row in the raw store."""
    from voiceai.common.pagination import PaginationParams

    page = await raw.list(PaginationParams(page=1, page_size=100))
    return list(page.items)


def _snapshot(*refs: str) -> _Snapshot:
    """Snapshot allowing exactly `refs`."""
    return _Snapshot(agent_id=AGENT_ID, tool_refs=list(refs))


async def test_schema_invalid_raises_and_logs_refusal() -> None:
    """Bad arguments raise 400 without any HTTP call, and are still audited."""
    executor, transport, _, _, raw = await _harness(
        [_row(parameters={"type": "object", "required": ["date"]})],
    )

    with pytest.raises(ToolExecutionError):
        await executor.execute_tool(
            tool_id=TOOL_ID, arguments={"nope": 1}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
        )

    assert transport.requests == []
    logs = await _log_rows(raw)
    assert len(logs) == 1
    assert logs[0].success is False
    assert logs[0].tool_id == TOOL_ID
    assert logs[0].error_id is not None


async def test_allowlist_deny_carries_identifiers_only() -> None:
    """Out-of-scope ids raise 403 whose message/details never echo arguments."""
    executor, transport, _, _, raw = await _harness([_row()])

    with pytest.raises(ToolDeniedError) as exc_info:
        await executor.execute_tool(
            tool_id=TOOL_ID,
            arguments={"api_key": SECRET},
            agent_snapshot=_snapshot("function:other"),
            tenant=TENANT,
        )

    assert transport.requests == []
    assert SECRET not in str(exc_info.value)
    assert SECRET not in str(exc_info.value.details)
    assert exc_info.value.details == {C.LOG_KEY_TOOL_ID: TOOL_ID, C.LOG_KEY_AGENT_ID: AGENT_ID}
    logs = await _log_rows(raw)
    assert len(logs) == 1
    assert logs[0].success is False
    assert logs[0].arguments == {"api_key": "***"}


async def test_ssrf_block_raises_without_http() -> None:
    """An unsafe endpoint is refused before any request, URL never in the error."""
    executor, transport, _, _, _ = await _harness([_row(url="http://169.254.169.254/latest")], guard=False)

    with pytest.raises(ToolExecutionError) as exc_info:
        await executor.execute_tool(tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT)

    assert transport.requests == []
    assert "169.254.169.254" not in str(exc_info.value)
    assert "169.254.169.254" not in str(exc_info.value.details)


async def test_unknown_tool_reads_as_404() -> None:
    """Missing rows answer 404 with identifiers only."""
    executor, transport, _, _, _ = await _harness([])

    with pytest.raises(ToolNotFoundError):
        await executor.execute_tool(tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT)

    assert transport.requests == []


async def test_401_then_success_refreshes_exactly_once() -> None:
    """A stale credential invalidates, refreshes, and retries one time."""
    cache = _Cache()
    store = _Store([{"token": "old-token"}, {"token": "new-token"}])
    executor, transport, _, _, raw = await _harness(
        [_row(auth_ref="cred-1")],
        script=[httpx.Response(401), httpx.Response(200, json={"booking": "ok"})],
        cache=cache,
        store=store,
    )

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={"date": "2026-01-01"}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is True
    assert result.data == {"booking": "ok"}
    assert len(transport.requests) == 2
    assert transport.requests[0].headers["authorization"] == "Bearer old-token"
    assert transport.requests[1].headers["authorization"] == "Bearer new-token"
    assert cache.deletes == [f"{C.AUTH_CACHE_KEY_PREFIX}cred-1"]
    assert store.fetches == 2
    logs = await _log_rows(raw)
    assert len(logs) == 1
    assert logs[0].success is True
    assert logs[0].tenant_id == TENANT


async def test_401_then_401_retries_only_once() -> None:
    """A repeated 401 answers failure after exactly two attempts (no retry loop)."""
    store = _Store([{"token": "old-token"}, {"token": "new-token"}])
    executor, transport, _, _, _ = await _harness(
        [_row(auth_ref="cred-1")],
        script=[httpx.Response(401), httpx.Response(401)],
        store=store,
    )

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is False
    assert result.error_id is not None
    assert len(transport.requests) == 2


async def test_401_without_credential_never_retries() -> None:
    """Bare endpoints return the 401 as-is (nothing to invalidate or refresh)."""
    executor, transport, _, store, _ = await _harness([_row()], script=[httpx.Response(401)])

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is False
    assert len(transport.requests) == 1
    assert store.fetches == 0


async def test_upstream_500_is_failure_value() -> None:
    """Non-2xx answers are `success=False` values (no exception text anywhere)."""
    executor, transport, _, _, _ = await _harness([_row()], script=[httpx.Response(500, json={"oops": True})])

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is False
    assert result.result_summary.startswith("HTTP 500 in ")
    assert len(transport.requests) == 1


async def test_timeout_is_failure_value_with_error_id() -> None:
    """A hung upstream answers `success=False` stitched to the log by `error_id`."""
    executor, transport, _, _, raw = await _harness([_row()], script=[httpx.ReadTimeout("slow")])

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is False
    assert result.error_id is not None
    assert "timed out" in result.result_summary
    assert len(transport.requests) == 1
    logs = await _log_rows(raw)
    assert logs[0].error_id == result.error_id


async def test_unreachable_is_failure_value() -> None:
    """Transport failures degrade to values (addresses never reach the caller)."""
    executor, _, _, _, _ = await _harness([_row()], script=[httpx.ConnectError("no route")])

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is False
    assert result.result_summary == C.EXECUTOR_UNREACHABLE_SUMMARY
    assert "no route" not in result.result_summary


async def test_internal_tool_runs_without_http() -> None:
    """Endpoint-less tools succeed with a redacted echo and a fingerprint."""
    executor, transport, _, _, raw = await _harness(
        [_row(url=None, kind="internal", tool_id="internal:hangup", name="hangup")]
    )

    result = await executor.execute_tool(
        tool_id="internal:hangup",
        arguments={"api_key": SECRET},
        agent_snapshot=_snapshot("internal:hangup"),
        tenant=TENANT,
    )

    assert result.success is True
    assert transport.requests == []
    assert result.data["arguments"] == {"api_key": "***"}
    assert result.call_fingerprint == fingerprint_tool_call("internal:hangup", {"api_key": SECRET})
    logs = await _log_rows(raw)
    assert logs[0].arguments == {"api_key": "***"}


async def test_mcp_namespaced_id_strips_to_canonical() -> None:
    """`mcp:{server}:{canonical}` executes against the canonical allowlist entry."""
    executor, transport, _, _, _ = await _harness([_row()], script=[httpx.Response(200, json={"ok": True})])

    result = await executor.execute_tool(
        tool_id="mcp:calendar:function:book",
        arguments={},
        agent_snapshot=_snapshot(TOOL_ID),
        tenant=TENANT,
    )

    assert result.success is True
    assert result.tool_id == TOOL_ID
    assert len(transport.requests) == 1


async def test_get_method_carries_query_params() -> None:
    """GET tools send arguments as query params, never as a JSON body."""
    executor, transport, _, _, _ = await _harness(
        [_row(method="GET")], script=[httpx.Response(200, json={"slots": []})]
    )

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={"date": "2026-01-01"}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is True
    assert transport.requests[0].url.params["date"] == "2026-01-01"


async def test_non_mapping_json_falls_back_to_text() -> None:
    """List bodies degrade to a bounded text payload instead of failing."""
    executor, _, _, _, _ = await _harness([_row()], script=[httpx.Response(200, json=["a", "b"])])

    result = await executor.execute_tool(
        tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT
    )

    assert result.success is True
    assert result.data[C.EXECUTOR_TEXT_KEY].startswith("[")


async def test_log_fail_open_returns_result_anyway() -> None:
    """A dead audit store never fails the execution it was meant to record."""
    executor, transport, _, _, _ = await _harness(
        [_row(url=None, kind="internal", tool_id="internal:hangup", name="hangup")],
        failing_logs=True,
    )

    result = await executor.execute_tool(
        tool_id="internal:hangup", arguments={}, agent_snapshot=_snapshot("internal:hangup"), tenant=TENANT
    )

    assert result.success is True
    assert transport.requests == []


async def test_resolver_cache_hit_skips_store() -> None:
    """Warm cache answers without touching the store (Phase-2 unwired safe)."""
    cache = _Cache()
    store = _Store([{"token": "cached-token"}])
    auth = ToolAuthResolver(redis_cache=cache, credential_store=store)
    tool = _row(auth_ref="cred-1")

    first = await auth.resolve_headers(tool)
    orphan = ToolAuthResolver(redis_cache=cache, credential_store=None)
    second = await orphan.resolve_headers(tool)

    assert first == {"authorization": "Bearer cached-token"}
    assert second == first
    assert store.fetches == 1


async def test_resolver_force_refresh_bypasses_stale_cache() -> None:
    """The 401 path reads fresh credentials even when the cache is warm."""
    cache = _Cache({f"{C.AUTH_CACHE_KEY_PREFIX}cred-1": '{"authorization": "Bearer stale"}'})
    store = _Store([{"token": "fresh"}])
    auth = ToolAuthResolver(redis_cache=cache, credential_store=store)

    headers = await auth.resolve_headers(_row(auth_ref="cred-1"), force_refresh=True)

    assert headers == {"authorization": "Bearer fresh"}
    assert store.fetches == 1


async def test_resolver_without_ref_answers_empty() -> None:
    """Tools without `auth_ref` never touch cache or store."""
    cache, store = _Cache(), _Store()
    auth = ToolAuthResolver(redis_cache=cache, credential_store=store)

    assert await auth.resolve_headers(_row()) == {}
    assert cache.gets == []
    assert store.fetches == 0


async def test_resolver_unknown_shapes_fail_closed() -> None:
    """Unusable credentials refuse with identifiers only (never bare calls)."""
    auth = ToolAuthResolver(credential_store=_Store([{"weird": 1}]))

    with pytest.raises(ToolExecutionError) as exc_info:
        await auth.resolve_headers(_row(auth_ref="cred-1"))

    assert exc_info.value.details == {C.AUTH_REF_FIELD: "cred-1"}


async def test_resolver_missing_store_fails_closed() -> None:
    """No vault wired yet: credentialed tools refuse instead of calling bare."""
    auth = ToolAuthResolver()

    with pytest.raises(ToolExecutionError):
        await auth.resolve_headers(_row(auth_ref="cred-1"))


async def test_resolver_degraded_cache_still_resolves() -> None:
    """A dead Redis degrades to the store (fail-open cache, fail-closed auth)."""
    auth = ToolAuthResolver(redis_cache=_Cache(fail=True), credential_store=_Store([{"token": "t"}]))

    headers = await auth.resolve_headers(_row(auth_ref="cred-1"))

    assert headers == {"authorization": "Bearer t"}
    await auth.invalidate_credential_cache("cred-1")


async def test_resolver_corrupt_cache_entry_is_a_miss() -> None:
    """Non-JSON cache entries fall through to the store and get overwritten."""
    cache = _Cache({f"{C.AUTH_CACHE_KEY_PREFIX}cred-1": "not-json{{{"})
    auth = ToolAuthResolver(redis_cache=cache, credential_store=_Store([{"api_key": "k"}]))

    assert await auth.resolve_headers(_row(auth_ref="cred-1")) == {C.AUTH_API_KEY_HEADER: "k"}


async def test_resolver_api_key_and_curated_headers_shapes() -> None:
    """Both remaining envelope shapes render without leaking values to logs."""
    auth = ToolAuthResolver(credential_store=_Store([{"api_key": "k"}]))
    assert await auth.resolve_headers(_row(auth_ref="a")) == {C.AUTH_API_KEY_HEADER: "k"}

    curated = ToolAuthResolver(credential_store=_Store([{"headers": {"X-Tool": "v", "n": 1}}]))
    assert await curated.resolve_headers(_row(auth_ref="b")) == {"X-Tool": "v"}


async def test_executor_writes_tenant_scoped_audit_rows() -> None:
    """Audit rows carry the acting tenant (container scopes the view per request)."""
    executor, _, _, _, raw = await _harness([_row()], script=[httpx.Response(200, json={"ok": True})])

    await executor.execute_tool(tool_id=TOOL_ID, arguments={}, agent_snapshot=_snapshot(TOOL_ID), tenant=TENANT)

    logs = await _log_rows(raw)
    assert len(logs) == 1
    assert logs[0].tenant_id == TENANT


def test_pure_helpers_are_deterministic() -> None:
    """Schema subset, namespace strip, and fingerprint pins (no I/O)."""
    assert validate_arguments({}, {"anything": 1}) == []
    assert validate_arguments({"required": ["date"]}, {}) == ["missing required: date"]
    assert validate_arguments({"properties": {"n": {"type": "integer"}}}, {"n": "x"}) == ["n: expected integer"]
    assert validate_arguments({"properties": {"n": {"type": "integer"}}}, {"n": True}) == ["n: expected integer"]
    assert validate_arguments({"properties": {"x": {"type": "mystery"}}}, {"x": 1}) == []
    assert strip_mcp_prefix("mcp:server:function:book") == "function:book"
    assert strip_mcp_prefix("mcp:book") == "book"
    assert strip_mcp_prefix("mcp:") == ""
    assert strip_mcp_prefix("function:book") == "function:book"
    assert strip_mcp_prefix("") == ""
    left = fingerprint_tool_call("function:book", {"b": 1, "a": 2})
    assert left == fingerprint_tool_call("function:book", {"a": 2, "b": 1})
    assert left != fingerprint_tool_call("function:book", {"a": 2, "b": 3})
    assert left != fingerprint_tool_call("function:other", {"a": 2, "b": 1})


async def test_executor_satisfies_voice_port_statically() -> None:
    """The executor structurally satisfies the voice seam (checked by mypy below)."""
    from voiceai.modules.voice.adapters.tools_port import AgentSnapshotPort, ToolExecutorPort

    executor, _, _, _, _ = await _harness([_row()])
    port: ToolExecutorPort = executor
    snapshot: AgentSnapshotPort = _Snapshot(tool_refs=[TOOL_ID])
    assert port is executor
    assert snapshot.agent_id == AGENT_ID
