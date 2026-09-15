"""MongoDB-backed repository tests (persistence cutover).

Run against a live MongoDB (local `docker compose up -d mongo`, or the CI
`mongo` service at `MONGO_URL`). Each test uses an isolated database and
skips cleanly when Mongo is unreachable, so the suite stays green offline.
"""

import os
import uuid

import pytest
from pymongo import AsyncMongoClient

from voiceai.core import db as db_factory
from voiceai.core.environment import get_mongo_db, get_mongo_url, get_platform_store_backend
from voiceai.database.constants import COLLECTIONS
from voiceai.errors import ConfigurationError
from voiceai.platform import models as platform_models
from voiceai.platform.models import BatchStatus, Execution, ExecutionStatus
from voiceai.platform.repositories import PlatformRepository
from voiceai.platform.store import MemoryStore


async def _mongo_reachable(url: str) -> bool:
    """Probe Mongo without raising.

    Args:
        url: Connection URL.

    Returns:
        True when a ping succeeds.
    """
    try:
        client = AsyncMongoClient(url, serverSelectionTimeoutMS=2000)
        await client.admin.command("ping")
        await client.close()
        return True
    except Exception:
        return False


@pytest.fixture()
async def mongo_store() -> PlatformRepository:
    """Isolated MongoStore on a throwaway database (skips offline)."""
    pytest.importorskip("voiceai.platform.mongo_store", reason="MongoStore not implemented yet")
    from voiceai.platform.mongo_store import MongoStore

    # Pinned to localhost: never run suite writes against Atlas/shared data.
    url = os.getenv("MONGO_TEST_URL", "mongodb://localhost:27017")
    if not await _mongo_reachable(url):
        pytest.skip("MongoDB unreachable (start it: docker compose up -d mongo)")
    db_name = f"{get_mongo_db()}_test_{uuid.uuid4().hex[:8]}"
    store = await MongoStore.connect(url, db_name, platform_models.ALL_DOCUMENT_MODELS)
    yield store
    await store.drop_database()
    await store.close()


async def test_mongo_execution_round_trip(mongo_store: PlatformRepository) -> None:
    """Save/get/list/count executions behave like the memory backend."""
    assert isinstance(mongo_store, PlatformRepository)
    memory = MemoryStore()
    execution = Execution(execution_id="exec-m1", agent_id="a1", to_number="+1", org_id="org-a")
    await memory.save_execution(execution)
    await mongo_store.save_execution(execution)
    found = await mongo_store.get_execution("exec-m1")
    assert found is not None and found.execution_id == "exec-m1"
    assert found.org_id == "org-a"
    listed = await mongo_store.list_executions(agent_id="a1", org_id="org-a")
    assert [e.execution_id for e in listed] == ["exec-m1"]
    assert await mongo_store.count_executions(agent_id="a1", org_id="org-a") == 1
    assert await mongo_store.get_execution("missing") is None


async def test_mongo_batch_claim_is_atomic(mongo_store: PlatformRepository) -> None:
    """Concurrent starts claim once; losers conflict, replays return the batch."""
    import asyncio

    from voiceai.errors import ConflictError
    from voiceai.platform.models import Batch, BatchStatus

    batch = Batch(batch_id="batch-m1", agent_id="a1", name="t", org_id="org-a")
    await mongo_store.save_batch(batch)
    results = await asyncio.gather(
        *[mongo_store.try_claim_batch_start("batch-m1", f"key-{i}") for i in range(5)],
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    assert len(winners) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 4
    winner_batch, winner_claimed = winners[0]
    assert winner_claimed is True
    replayed, replay_claimed = await mongo_store.try_claim_batch_start("batch-m1", winner_batch.idempotency_key)
    assert replay_claimed is False
    assert replayed.batch_id == "batch-m1"


async def test_mongo_unique_email_backstop(mongo_store: PlatformRepository) -> None:
    """Duplicate user emails are rejected (unique index, not silent overwrite)."""
    from voiceai.platform.models import User

    await mongo_store.save_user(User(user_id="u-m1", email="dup@example.com", password_hash="ph"))
    with pytest.raises(Exception):
        await mongo_store.save_user(User(user_id="u-m2", email="dup@example.com", password_hash="ph"))


async def test_mongo_wallet_topup_atomic(mongo_store: PlatformRepository) -> None:
    """Concurrent topups never lose updates (server-side atomicity)."""
    import asyncio
    from decimal import Decimal

    await asyncio.gather(*[mongo_store.topup_wallet_credits(Decimal("1.00")) for _ in range(10)])
    wallet = await mongo_store.get_wallet()
    assert float(wallet.balance_credits) == 10.0


async def test_mongo_reset_preserves_auth_collections(mongo_store: PlatformRepository) -> None:
    """reset_platform wipes domain data but never auth collections."""
    from voiceai.platform.models import User

    await mongo_store.save_user(User(user_id="u-m9", email="keep@example.com", password_hash="ph"))
    await mongo_store.save_execution(Execution(execution_id="exec-m9", agent_id="a", to_number="+1"))
    cleared = await mongo_store.reset_platform()
    assert cleared.get(COLLECTIONS["EXECUTIONS"], 0) >= 1
    assert await mongo_store.get_user("u-m9") is not None
    assert await mongo_store.get_execution("exec-m9") is None


async def test_mongo_service_flow_parity(mongo_store: PlatformRepository) -> None:
    """A full service flow works end-to-end on the mongo backend."""
    from voiceai.platform import services
    from voiceai.platform.auth import Principal
    from voiceai.platform.models import BatchEntry, CreateBatchRequest

    principal = Principal(user_id="u1", email="a@example.com", org_id="org-a", role="admin")
    created = await services.create_batch(
        mongo_store, principal, CreateBatchRequest(agent_id="a1", name="t", entries=[BatchEntry(to_number="+1")])
    )
    started = await services.start_batch(mongo_store, principal, created.batch_id, delay_scale=0)
    assert started.batch_id == created.batch_id
    stopped = await services.stop_batch(mongo_store, principal, created.batch_id)
    assert stopped.status.value == "stopped"
    await services.delete_batch(mongo_store, principal, created.batch_id)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await services.get_batch(mongo_store, principal, created.batch_id)


async def test_execution_status_values_unchanged() -> None:
    """Wire enum values are frozen by the cutover (no silent renames)."""
    assert ExecutionStatus.COMPLETED.value == "completed"
    assert BatchStatus.RUNNING.value == "running"


async def test_all_document_models_resolve_to_registry() -> None:
    """Every Beanie document model points at a registered collection."""
    known = set(COLLECTIONS.values())
    assert platform_models.ALL_DOCUMENT_MODELS, "no document models registered"
    for model in platform_models.ALL_DOCUMENT_MODELS:
        assert model.Settings.name in known
    assert len({m.Settings.name for m in platform_models.ALL_DOCUMENT_MODELS}) == len(
        platform_models.ALL_DOCUMENT_MODELS
    ), "two models share a collection"


async def test_platform_backend_accessor_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backend selection defaults to memory and rejects unknown values."""
    monkeypatch.delenv("PLATFORM_STORE_BACKEND", raising=False)
    assert get_platform_store_backend() == "memory"
    monkeypatch.setenv("PLATFORM_STORE_BACKEND", "mongo")
    assert get_platform_store_backend() == "mongo"
    monkeypatch.setenv("PLATFORM_STORE_BACKEND", "carrier-pigeon")
    with pytest.raises(ConfigurationError):
        get_platform_store_backend()
