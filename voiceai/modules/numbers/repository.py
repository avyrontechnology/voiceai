"""Phone number storage (spec 0074).

The one non-obvious behaviour here is the legacy-envelope fallback: rows
written before the migration sit under `{payload: {...}}`, so a read unwraps
them. That fallback is removed by the spec that flattens the collection — it is
a migration bridge, not permanent behaviour.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.repository import BaseRepository
from voiceai.modules.numbers.constants import SUBSCRIBER_DIGITS_LENGTH
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.static_methods import normalize_phone_digits


class PhoneNumberRepository:
    """Tenant-scoped storage for phone numbers.

    Args:
        repository: The `BaseRepository[PhoneNumber]` over the numbers collection.
    """

    def __init__(self, repository: BaseRepository[PhoneNumber]) -> None:
        self._repo = repository

    async def create(self, number: PhoneNumber) -> PhoneNumber:
        """Persist a new number and return the stored copy.

        Args:
            number: The number to insert.

        Returns:
            The inserted number, with `id` and tenant stamp assigned by the repository.
        """
        return await self._repo.insert(number)

    async def get(self, number_id: str) -> PhoneNumber | None:
        """Return the number with this id, or `None` when absent.

        Args:
            number_id: Storage identifier.
        """
        return await self._repo.get(number_id)

    async def list_all(self) -> list[PhoneNumber]:
        """Return every active number in the tenant, in either stored shape.

        Reads through ``raw_documents`` rather than ``walk_pages`` on purpose: rows
        written before spec 0074 sit under a ``{payload: {...}}`` envelope and do not
        validate as ``PhoneNumber``, so a typed listing would silently drop them — and a
        dropped number is an inbound call that stops routing. ``_as_number`` converts
        both shapes.
        """
        found: list[PhoneNumber] = []
        for row in await self._repo.raw_documents():
            record = _as_number(row)
            if record is not None:
                found.append(record)
        return found

    async def update(self, number: PhoneNumber) -> PhoneNumber:
        """Persist changes to an existing number.

        Args:
            number: The number carrying the new values.

        Returns:
            The stored number.
        """
        return await self._repo.update(number)

    async def delete(self, number_id: str) -> bool:
        """Soft-delete the number with this id.

        Args:
            number_id: Storage identifier.

        Returns:
            True when an active row was tombstoned, False when there was none.
        """
        return await self._repo.soft_delete(number_id)

    async def restore(self, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one row in place unless it already exists (backfill only).

        The Upstash→Atlas migration calls this to move legacy numbers across
        without going through `create`, because a restore must preserve the
        original id and must never overwrite a row the database already holds.

        Args:
            item_id: The legacy row id, used verbatim as the document id.
            payload: The stored number document.

        Returns:
            True when the row was written, False when one was already there.
        """
        if await self._repo.get(item_id) is not None:
            return False
        await self._repo.insert(PhoneNumber(id=item_id, **payload))
        return True

    async def find_by_digits(self, digits: str) -> PhoneNumber | None:
        """Resolve a dialled DID to its stored number (the Talko lookup).

        Matches on normalized digits, then falls back to a last-10 comparison for
        legacy rows stored without a country prefix — the same two-pass lookup the
        legacy store performed, so no routable number stops resolving.

        Args:
            digits: The DID as dialled, in any format.

        Returns:
            The matching number, or `None` when nothing matches.
        """
        needle = normalize_phone_digits(digits)
        if not needle:
            return None

        candidates = await self.list_all()
        for candidate in candidates:
            if normalize_phone_digits(candidate.number) == needle:
                return candidate

        if len(needle) >= SUBSCRIBER_DIGITS_LENGTH:
            last_digits = needle[-SUBSCRIBER_DIGITS_LENGTH:]
            for candidate in candidates:
                stored = normalize_phone_digits(candidate.number)
                if len(stored) < SUBSCRIBER_DIGITS_LENGTH:
                    continue
                if stored[-SUBSCRIBER_DIGITS_LENGTH:] == last_digits:
                    return candidate
        return None


def _as_number(row: Any) -> PhoneNumber | None:
    """Read a stored row as a `PhoneNumber`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0074 envelope shape.

    Returns:
        The typed number, or `None` when the row cannot be read as one.
    """
    if isinstance(row, PhoneNumber):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        # Legacy `PlatformRow`: the document nested under `payload`, with the
        # envelope's `id` as the number_id the resolve path logs.
        legacy_id = data.get("id")
        data = {
            **payload,
            "id": payload.get("number_id", legacy_id),
        }
    try:
        return PhoneNumber(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None


# legacy-shim(spec-0074): kept as the public helper name; both now read either shape.
unwrap_legacy = _as_number
