"""Time handling for the whole project (AGENTS.md rule 2): no naked `datetime.now()`.

Every timestamp the system stores or emits is timezone-aware UTC. Naive datetimes are a
recurring source of off-by-hours bugs once a process, a database and a client disagree about
the local zone, so the helpers here accept naive input only by declaring it UTC explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

from voiceai.common.constants import (
    IST_OFFSET_HOURS,
    IST_OFFSET_MINUTES,
    MILLISECONDS_PER_SECOND,
    UTC_ISO_SUFFIX,
    ZULU_SUFFIX,
)
from voiceai.common.errors import InvalidRequestError

__all__ = ["IST", "UTC", "epoch_ms", "isoformat_z", "parse_iso", "to_ist", "utc_now"]

UTC: Final[timezone] = timezone.utc
#: India Standard Time as a fixed offset — no DST, so no zoneinfo/tzdata dependency is needed.
IST: Final[timezone] = timezone(timedelta(hours=IST_OFFSET_HOURS, minutes=IST_OFFSET_MINUTES))

_INVALID_ISO_MESSAGE: Final[str] = "Invalid ISO-8601 timestamp"


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime.

    Returns:
        `datetime.now` in UTC — the only clock read the project makes.
    """
    return datetime.now(tz=UTC)


def _as_aware_utc(value: datetime) -> datetime:
    """Return `value` as aware UTC, treating a naive input as UTC.

    Args:
        value: Aware or naive datetime.

    Returns:
        The same instant, expressed in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def isoformat_z(value: datetime) -> str:
    """Render a datetime as an ISO-8601 string with the `Z` suffix.

    Args:
        value: The datetime to render; naive input is treated as UTC.

    Returns:
        E.g. `2026-09-16T10:30:00+00:00` rendered as `2026-09-16T10:30:00Z`, which every
        JavaScript client parses without a shim.
    """
    rendered = _as_aware_utc(value).isoformat()
    if rendered.endswith(UTC_ISO_SUFFIX):
        return f"{rendered[: -len(UTC_ISO_SUFFIX)]}{ZULU_SUFFIX}"
    return rendered


def parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Accepts the `Z` suffix, which `datetime.fromisoformat` only learned in Python 3.11.

    Args:
        text: The timestamp to parse.

    Returns:
        The parsed instant in UTC; a timestamp without an offset is read as UTC.

    Raises:
        InvalidRequestError: When the text is not a valid ISO-8601 timestamp. Converted here so
            a `ValueError` never crosses a layer boundary (AGENTS.md rule 1c).
    """
    normalized = text.strip()
    if normalized.endswith(ZULU_SUFFIX):
        normalized = f"{normalized[: -len(ZULU_SUFFIX)]}{UTC_ISO_SUFFIX}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidRequestError(_INVALID_ISO_MESSAGE, cause=exc) from exc
    return _as_aware_utc(parsed)


def to_ist(value: datetime) -> datetime:
    """Convert a datetime to India Standard Time for display.

    Args:
        value: The datetime to convert; naive input is treated as UTC.

    Returns:
        The same instant at UTC+05:30. Storage stays UTC — this is a presentation helper.
    """
    return _as_aware_utc(value).astimezone(IST)


def epoch_ms(value: datetime | None = None) -> int:
    """Return milliseconds since the Unix epoch.

    Args:
        value: The instant to convert; `None` means now.

    Returns:
        Whole milliseconds, truncated — the unit Redis TTLs and client metrics expect.
    """
    moment = _as_aware_utc(value) if value is not None else utc_now()
    return int(moment.timestamp() * MILLISECONDS_PER_SECOND)
