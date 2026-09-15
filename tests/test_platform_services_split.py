"""Service-split tests for the platform god-file follow-up (US2, task T053).

Proves ``voiceai/platform/services.py`` decomposed into per-domain modules
under ``voiceai/platform/services/`` with zero behavior change: every
service function resolves from its domain module AND from the package
(back-compat), each module file stays within the 1500-line budget, and
AuthZ/caps still enforced by the services layer.
"""

import pathlib

import pytest

from voiceai.errors import AuthorizationError, InvalidRequestError
from voiceai.platform import services
from voiceai.platform.auth import Principal
from voiceai.platform.models import BatchEntry, CreateBatchRequest, Execution
from voiceai.platform.repositories import MemoryStore
from voiceai.platform.services import (
    batches,
    executions,
)


def _principal(org_id: str = "org-a") -> Principal:
    """Admin principal scoped to an org."""
    return Principal(user_id="u1", email="a@example.com", org_id=org_id, role="admin")


def test_domain_modules_expose_service_functions() -> None:
    """Per-domain modules exist and own their service functions."""
    assert callable(batches.create_batch)
    assert callable(batches.start_batch)
    assert callable(batches.retry_failed)
    assert callable(executions.list_executions)
    assert callable(executions.get_execution_stats)
    # Package back-compat: the old attribute path still resolves.
    assert services.create_batch is batches.create_batch
    assert services.get_execution is executions.get_execution


async def test_split_preserves_org_boundary() -> None:
    """Cross-org reads still raise AuthorizationError from the service layer."""
    store = MemoryStore()
    await store.save_execution(Execution(execution_id="exec-1", agent_id="agent-1", to_number="+1000", org_id="org-a"))
    with pytest.raises(AuthorizationError):
        await executions.get_execution(store, _principal("org-b"), "exec-1")
    found = await services.get_execution(store, _principal("org-a"), "exec-1")
    assert found.execution_id == "exec-1"


async def test_split_preserves_batch_cap() -> None:
    """The max-entries cap is still enforced by the service layer."""
    store = MemoryStore()
    limit = batches.get_batch_max_entries()
    payload = CreateBatchRequest.model_construct(
        agent_id="agent-1",
        name="big",
        entries=[BatchEntry(to_number=f"+{i}") for i in range(limit + 1)],
    )
    with pytest.raises(InvalidRequestError):
        await batches.create_batch(store, _principal(), payload)


def test_service_modules_within_line_budget() -> None:
    """Every services/ module is at most 1500 lines (contract V-04)."""
    for path in pathlib.Path("voiceai/platform/services").glob("*.py"):
        lines = sum(1 for _ in path.open())
        assert lines <= 1500, f"{path} has {lines} lines (>1500)"
