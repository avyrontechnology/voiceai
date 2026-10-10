"""Retention units: TTL selection, isolation, and the consent exclusion (spec 0073).

The expiry matrix runs against the *real* container builder over the in-memory
driver, so the test pins the wiring that production uses (unscoped repositories,
env TTLs) rather than a hand-rolled fake that could drift from it. The
consent-exclusion test is the security pin from the spec: a future edit that
registers `CONSENT_RECORDS` as a purge target must fail the suite.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from voiceai.common.datetime_utils import utc_now
from voiceai.core.container import _build_retention_service
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.privacy.models import PrivacyAuditRecord
from voiceai.modules.privacy.retention import RetentionService

DAY_S = 86400


class _FakeEnvironment:
    """The four retention knobs the builder reads."""

    def __init__(self, **overrides: int) -> None:
        self.retention_privacy_audit_s = overrides.get("audit", 30 * DAY_S)
        self.retention_chat_sessions_s = overrides.get("chat", 365 * DAY_S)
        self.retention_tool_execution_logs_s = overrides.get("tools", 365 * DAY_S)
        self.retention_interval_s = overrides.get("interval", DAY_S)


def _service(db: InMemoryDatabase, **overrides: int) -> Any:
    """Build the real container-level retention service over the in-memory driver."""
    return _build_retention_service(db, _FakeEnvironment(**overrides))


async def _seed(repo: Any, count: int, *, age_s: int, active: bool = True) -> list[Any]:
    """Insert `count` audit rows last written `age_s` seconds ago."""
    rows = []
    stamp = utc_now() - timedelta(seconds=age_s)
    for index in range(count):
        row = PrivacyAuditRecord(subject_id=f"sub-{index}", action="erasure", actor_id="operator-1")
        row.updated_at = stamp
        row.is_active = active
        rows.append(await repo.insert(row))
    return rows


async def _audit_repo(db: InMemoryDatabase) -> Any:
    return InMemoryRepository(db, Collections.PRIVACY_AUDIT_LOG, PrivacyAuditRecord)


async def test_expired_rows_are_soft_erased() -> None:
    """A row older than the TTL is tombstoned, not destroyed."""
    db = InMemoryDatabase()
    repo = await _audit_repo(db)
    await _seed(repo, 2, age_s=40 * DAY_S)

    results = await _service(db).run_cycle()

    assert results["privacy_audit_log"] == 2
    remaining = await repo.list_where({}, _page())
    assert remaining.items == []


async def test_fresh_rows_are_untouched() -> None:
    """A row inside its TTL survives the pass."""
    db = InMemoryDatabase()
    repo = await _audit_repo(db)
    await _seed(repo, 3, age_s=DAY_S)

    results = await _service(db).run_cycle()

    assert results["privacy_audit_log"] == 0
    assert len((await repo.list_where({}, _page())).items) == 3


async def test_row_just_inside_the_ttl_is_kept() -> None:
    """A row written moments before its TTL expires survives the pass.

    Seeded just *inside* the window, not exactly on the boundary: `run_cycle`
    re-reads the clock, so an exactly-at-TTL row would be expired by the
    microseconds that elapse between seeding and the pass. The purge predicate
    is "strictly older than the TTL", which this pins from the safe side.
    """
    db = InMemoryDatabase()
    repo = await _audit_repo(db)
    await _seed(repo, 1, age_s=30 * DAY_S - 60)

    results = await _service(db, audit=30 * DAY_S).run_cycle()

    assert results["privacy_audit_log"] == 0


async def test_inactive_rows_are_not_recounted() -> None:
    """An already-tombstoned row is skipped: the walk only yields active rows."""
    db = InMemoryDatabase()
    repo = await _audit_repo(db)
    await _seed(repo, 2, age_s=40 * DAY_S, active=False)

    results = await _service(db).run_cycle()

    assert results["privacy_audit_log"] == 0


async def test_disabled_collection_is_skipped() -> None:
    """TTL `0` disables that collection's purge without raising."""
    db = InMemoryDatabase()
    await _seed(await _audit_repo(db), 2, age_s=400 * DAY_S)

    results = await _service(db, audit=0).run_cycle()

    assert results["privacy_audit_log"] == 0
    assert results["chat_sessions"] == 0


async def test_consent_records_are_never_purgeable() -> None:
    """The security pin: no consent TTL exists and consent is absent from the registry."""
    db = InMemoryDatabase()
    service = _service(db)

    registered = set(service._targets)  # noqa: SLF001 - the registry is the pinned contract

    assert "consent_records" not in registered
    assert not any(name.startswith("consent") for name in registered)
    # No knob exists to turn consent purging on.
    assert not [f for f in Environment.model_fields if f.startswith("retention_consent")]


async def test_one_failing_collection_does_not_stop_the_cycle() -> None:
    """A purge that raises is isolated: the other collections still run (AGENTS.md §5)."""
    db = InMemoryDatabase()
    service = RetentionService(
        {
            "boom": (DAY_S, _raise),
            "chat_sessions": (0, _never_called),
        }
    )

    results = await service.run_cycle()

    assert results == {"boom": 0, "chat_sessions": 0}


async def test_cycle_returns_a_count_per_registered_collection() -> None:
    """Every registered collection reports a count, so a partial pass is visible."""
    db = InMemoryDatabase()

    results = await _service(db).run_cycle()

    assert set(results) == {"privacy_audit_log", "chat_sessions", "tool_execution_logs"}


def _page() -> Any:
    """A single unbounded-enough page for direct list assertions."""
    from voiceai.common.pagination import PaginationParams

    return PaginationParams(page=1, page_size=100)


async def _raise(_cutoff: Any) -> int:
    raise RuntimeError("driver unavailable")


async def _never_called(_cutoff: Any) -> int:
    pytest.fail("a disabled collection must not be purged")
