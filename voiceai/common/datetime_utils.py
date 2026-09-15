"""Shared UTC datetime utilities (Constitution V).

All timestamps in the system are timezone-aware UTC. Naive datetimes are
rejected at every boundary rather than silently reinterpreted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Union


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Returns:
        ``datetime.now(timezone.utc)``.
    """
    return datetime.now(timezone.utc)


def format_datetime(value: datetime) -> str:
    """Format a tz-aware datetime as an ISO-8601 string.

    Args:
        value: A timezone-aware datetime.

    Returns:
        The ISO-8601 representation.

    Raises:
        ValueError: If ``value`` is naive.
    """
    _require_aware(value)
    return value.isoformat()


def parse_datetime(value: Union[str, datetime]) -> datetime:
    """Parse an ISO-8601 string (or pass through) a tz-aware datetime.

    Args:
        value: ISO-8601 text with an offset, or an aware datetime.

    Returns:
        The timezone-aware datetime.

    Raises:
        ValueError: If the input is naive, empty, or unparsable.
    """
    if isinstance(value, datetime):
        return _require_aware(value)
    if not value or not value.strip():
        raise ValueError("empty datetime value")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"unparsable datetime: {value!r}") from exc
    return _require_aware(parsed)


def _require_aware(value: datetime) -> datetime:
    """Reject naive datetimes.

    Args:
        value: The datetime to check.

    Returns:
        ``value`` unchanged when aware.

    Raises:
        ValueError: If ``value`` has no timezone info.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"naive datetime rejected (must be tz-aware UTC): {value!r}")
    return value
