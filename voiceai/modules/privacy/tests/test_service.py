"""Privacy service units over a fake repository (spec 0071).

Grant idempotency, read filtering, and withdrawal tombstones — all without a
database, so the contract stays pinned even when the driver changes.
"""

from __future__ import annotations

import pytest

from voiceai.modules.privacy.errors import ConsentNotFoundError, UnknownPurposeError
from voiceai.modules.privacy.models import ConsentRecord
from voiceai.modules.privacy.repository import ConsentRepository
from voiceai.modules.privacy.service import PrivacyService


class _FakeStore:
    """In-memory `BaseRepository` surface the consent repository needs."""

    def __init__(self) -> None:
        self.rows: dict[str, ConsentRecord] = {}
        self._next = 0

    async def insert(self, model: ConsentRecord) -> ConsentRecord:
        if model.id is None:
            self._next += 1
            model.id = f"consent-{self._next}"
        self.rows[model.id] = model
        return model

    async def find_many(self, field: str, value: object) -> list[ConsentRecord]:
        return [row for row in self.rows.values() if getattr(row, field) == value]


def _service() -> PrivacyService:
    return PrivacyService(ConsentRepository(_FakeStore()))  # type: ignore[arg-type]


async def test_grant_happy() -> None:
    """A grant persists an active row with the policy version."""
    service = _service()

    record = await service.grant(subject_id="sub-1", purpose="analytics", policy_version="v3")

    assert record.granted is True
    assert record.policy_version == "v3"
    assert record.withdrawn_at is None


async def test_grant_unknown_purpose_400() -> None:
    """A purpose outside the vocabulary raises before any write."""
    service = _service()

    with pytest.raises(UnknownPurposeError):
        await service.grant(subject_id="sub-1", purpose="surveillance", policy_version="v3")


async def test_regrant_is_idempotent() -> None:
    """Re-granting an active pair returns the same row (no duplicate)."""
    service = _service()

    first = await service.grant(subject_id="sub-1", purpose="marketing", policy_version="v3")
    second = await service.grant(subject_id="sub-1", purpose="marketing", policy_version="v4")

    assert first.id == second.id
    assert second.policy_version == "v3"


async def test_reads_exclude_withdrawn() -> None:
    """Withdrawn rows never appear in reads."""
    service = _service()
    await service.grant(subject_id="sub-1", purpose="analytics", policy_version="v3")
    await service.grant(subject_id="sub-1", purpose="marketing", policy_version="v3")
    await service.withdraw(subject_id="sub-1", purpose="marketing")

    rows = await service.list_consents(subject_id="sub-1")

    assert [row.purpose for row in rows] == ["analytics"]


async def test_withdraw_happy_tombstones() -> None:
    """Withdrawal stamps granted=False + withdrawn_at; the row survives."""
    service = _service()
    await service.grant(subject_id="sub-1", purpose="analytics", policy_version="v3")

    tombstone = await service.withdraw(subject_id="sub-1", purpose="analytics")

    assert tombstone.granted is False
    assert tombstone.withdrawn_at is not None


async def test_withdraw_unknown_pair_404() -> None:
    """Withdrawing a pair with no active row raises the opaque 404."""
    service = _service()

    with pytest.raises(ConsentNotFoundError):
        await service.withdraw(subject_id="nobody", purpose="analytics")
