"""Service-layer tests for the platform pilot (US2, task T025).

Services are called directly with a MemoryStore — no HTTP involved.
Proves business logic + AuthZ live in services.py (Constitution I, IX).
"""

from typing import Any

import pytest
from fastapi import HTTPException

from voiceai.errors import AuthorizationError, ConflictError, InvalidRequestError
from voiceai.platform import services
from voiceai.platform.auth import Principal
from voiceai.platform.models import (
    BatchEntry,
    BatchStatus,
    CreateApiKeyRequest,
    CreateBatchRequest,
    Execution,
    ExecutionStatus,
)
from voiceai.platform.store import MemoryStore


def _principal(org_id: str = "org-a") -> Principal:
    """Admin principal scoped to an org."""
    return Principal(user_id="u1", email="a@example.com", org_id=org_id, role="admin")


def _execution(org_id: str, execution_id: str = "exec-1") -> Execution:
    """Minimal execution row for seeding."""
    return Execution(execution_id=execution_id, agent_id="agent-1", to_number="+1000", org_id=org_id)


async def test_get_execution_enforces_org_boundary() -> None:
    """Cross-org reads raise AuthorizationError from the service layer."""
    store = MemoryStore()
    await store.save_execution(_execution("org-a"))
    with pytest.raises(AuthorizationError):
        await services.get_execution(store, _principal("org-b"), "exec-1")
    found = await services.get_execution(store, _principal("org-a"), "exec-1")
    assert found.execution_id == "exec-1"


async def test_get_execution_missing_is_404() -> None:
    """Unknown ids raise a 404 HTTPException (envelope-mapped upstream)."""
    store = MemoryStore()
    with pytest.raises(HTTPException) as exc_info:
        await services.get_execution(store, _principal(), "nope")
    assert exc_info.value.status_code == 404


async def test_create_batch_rejects_oversize() -> None:
    """Batches beyond the max-entries cap raise InvalidRequestError."""
    store = MemoryStore()
    limit = services.get_batch_max_entries()
    # Bypass request validation (max_length=100) to prove the service
    # enforces the cap itself as defense-in-depth.
    payload = CreateBatchRequest.model_construct(
        agent_id="agent-1",
        name="big",
        entries=[BatchEntry(to_number=f"+{i}") for i in range(limit + 1)],
    )
    with pytest.raises(InvalidRequestError):
        await services.create_batch(store, _principal(), payload)


async def test_create_and_start_batch_idempotent_replay() -> None:
    """Start claims once; replaying the same Idempotency-Key returns it."""
    store = MemoryStore()
    created = await services.create_batch(
        store, _principal(), CreateBatchRequest(agent_id="agent-1", name="t", entries=[BatchEntry(to_number="+1")])
    )
    first = await services.start_batch(store, _principal(), created.batch_id, idempotency_key="k1", delay_scale=0)
    second = await services.start_batch(store, _principal(), created.batch_id, idempotency_key="k1", delay_scale=0)
    assert first.batch_id == created.batch_id
    assert second.batch_id == created.batch_id
    assert first.status != BatchStatus.DRAFT


async def test_stop_batch_transitions_to_stopped() -> None:
    """Stop parks a batch as STOPPED with an end timestamp."""
    store = MemoryStore()
    created = await services.create_batch(
        store, _principal(), CreateBatchRequest(agent_id="agent-1", name="t", entries=[BatchEntry(to_number="+1")])
    )
    stopped = await services.stop_batch(store, _principal(), created.batch_id)
    assert stopped.status == BatchStatus.STOPPED
    assert stopped.ended_at is not None


async def test_retry_failed_without_failures_conflicts() -> None:
    """Retrying a batch with no failed executions raises ConflictError."""
    store = MemoryStore()
    created = await services.create_batch(
        store, _principal(), CreateBatchRequest(agent_id="agent-1", name="t", entries=[BatchEntry(to_number="+1")])
    )
    with pytest.raises(ConflictError):
        await services.retry_failed(store, _principal(), created.batch_id)


async def test_retry_failed_builds_retry_batch() -> None:
    """Failed executions are re-queued into a new retry batch."""
    store = MemoryStore()
    created = await services.create_batch(
        store, _principal(), CreateBatchRequest(agent_id="agent-1", name="t", entries=[BatchEntry(to_number="+9")])
    )
    failed = _execution("org-a", "exec-failed")
    failed.batch_id = created.batch_id
    failed.status = ExecutionStatus.FAILED
    await store.save_execution(failed)
    retried = await services.retry_failed(store, _principal(), created.batch_id)
    assert retried.batch_id != created.batch_id
    assert len(retried.entries) == 1
    assert "(retry)" in retried.name


async def test_create_api_key_validates_scopes_and_returns_secret_once() -> None:
    """Unknown scopes 400; the full secret is returned once, hash stored."""
    store = MemoryStore()
    with pytest.raises(HTTPException) as exc_info:
        await services.create_api_key(store, _principal(), CreateApiKeyRequest(name="bad", scopes=["nope:not-a-scope"]))
    assert exc_info.value.status_code == 400
    created = await services.create_api_key(
        store, _principal(), CreateApiKeyRequest(name="good", scopes=["platform:read"])
    )
    assert created.key.startswith(created.prefix)
    stored = await store.get_api_key_by_hash(__import__("hashlib").sha256(created.key.encode()).hexdigest())
    assert stored is not None
    assert stored.key_hash != created.key


async def test_delete_missing_api_key_is_404() -> None:
    """Deleting an unknown key raises 404."""
    store = MemoryStore()
    with pytest.raises(HTTPException) as exc_info:
        await services.delete_api_key(store, _principal(), "nope")
    assert exc_info.value.status_code == 404


async def test_execution_stats_aggregate_seeded_rows() -> None:
    """Stats reflect executions created through the service."""
    store = MemoryStore()
    from voiceai.platform.models import SimulateCallRequest

    await services.simulate_call(
        store,
        SimulateCallRequest(agent_id="agent-1", to_number="+1", delay_scale=0),
        _principal(),
    )
    stats = await services.get_execution_stats(store, _principal())
    assert stats.total >= 1
    latency = await services.get_latency_stats(store, _principal())
    assert latency.count >= 1


async def test_delete_batch_cleans_up() -> None:
    """Delete removes the batch so reads go 404."""
    store = MemoryStore()
    created = await services.create_batch(
        store, _principal(), CreateBatchRequest(agent_id="agent-1", name="t", entries=[BatchEntry(to_number="+1")])
    )
    await services.delete_batch(store, _principal(), created.batch_id)
    with pytest.raises(HTTPException) as exc_info:
        await services.get_batch(store, _principal(), created.batch_id)
    assert exc_info.value.status_code == 404


async def test_service_layer_does_not_import_transport_state() -> None:
    """Services use injected store/principal args (no app.state access)."""
    import ast
    from pathlib import Path

    paths = sorted(Path("voiceai/platform/services").glob("*.py"))
    assert paths, "services package missing"
    for path in paths:
        tree = ast.parse(path.read_text())
        app_state_access = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "state"
            and isinstance(node.value, ast.Name)
            and node.value.id == "app"
        ]
        store_globals = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "platform_store"]
        assert not app_state_access, f"{path} reads app.state"
        assert not store_globals, f"{path} touches platform_store"


async def test_services_import_errors_via_module_surface() -> None:
    """Services import exception types from platform.exceptions, never voiceai.errors."""
    import ast
    from pathlib import Path

    paths = sorted(Path("voiceai/platform/services").glob("*.py"))
    assert paths, "services package missing"
    for path in paths:
        tree = ast.parse(path.read_text())
        direct = [
            node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "voiceai.errors"
        ]
        assert not direct, f"{path} imports voiceai.errors directly (use platform.exceptions)"
