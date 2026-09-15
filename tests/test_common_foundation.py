"""Tests for voiceai.common (US1, task T011).

Response envelope (reuses voiceai.responses shapes, never redefines them),
shared pagination behavior, and UTC datetime utilities.
"""

from datetime import datetime, timezone

import pytest

from voiceai.common.datetime_utils import format_datetime, parse_datetime, utcnow
from voiceai.common.pagination import Page, normalize_pagination
from voiceai.common.responses import error_response, internal_error, ok_response


def test_ok_response_envelope_shape() -> None:
    """Success bodies carry ok=True plus the payload as detail."""
    body = ok_response({"id": "123"})
    assert body["ok"] is True
    assert body["detail"] == {"id": "123"}


def test_error_response_envelope_shape() -> None:
    """Error bodies match the project ErrorEnvelope with redacted-safe fields."""
    envelope = error_response(
        "platform.api_key_revoked",
        "API key revoked",
        error_id="abc123",
        retryable=False,
        component="platform",
        details={"key_prefix": "sk-li"},
    )
    assert envelope.ok is False
    assert envelope.detail == "API key revoked"
    assert envelope.error.code == "platform.api_key_revoked"
    assert envelope.error.error_id == "abc123"
    assert envelope.error.retryable is False
    assert envelope.error.component == "platform"


def test_internal_error_hides_internals_but_keeps_ref() -> None:
    """Unexpected failures surface only as Internal error (ref <error_id>)."""
    envelope = internal_error("deadbeef")
    assert envelope.ok is False
    assert "deadbeef" in envelope.detail
    assert "Internal error" in envelope.detail
    assert "Traceback" not in envelope.detail


@pytest.mark.parametrize(
    ("page", "page_size", "expected"),
    [(1, 20, (1, 20)), (0, 20, (1, 20)), (-3, 20, (1, 20)), (2, 500, (2, 100)), (2, 0, (2, 20)), (2, -5, (2, 20))],
)
def test_normalize_pagination_clamps_bounds(page: int, page_size: int, expected: tuple[int, int]) -> None:
    """Pages start at 1; page_size defaults to 20 and caps at 100."""
    assert normalize_pagination(page, page_size) == expected


def test_page_model_carries_total_and_items() -> None:
    """Page wraps items with the total count for the listing."""
    page = Page(items=[1, 2], total=42, page=2, page_size=20)
    assert page.total == 42
    assert page.items == [1, 2]
    assert page.page == 2
    assert page.page_size == 20


def test_utcnow_is_tz_aware_utc() -> None:
    """utcnow() is always timezone-aware UTC, never naive local time."""
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def test_datetime_round_trip() -> None:
    """format/parse round-trips a tz-aware datetime exactly."""
    now = utcnow()
    assert parse_datetime(format_datetime(now)) == now


def test_parse_rejects_naive_datetimes() -> None:
    """Naive datetime strings are rejected (data-model Entity 1 rule)."""
    with pytest.raises(ValueError):
        parse_datetime("2026-09-15 12:00:00")
    with pytest.raises(ValueError):
        parse_datetime(datetime(2026, 9, 15, 12, 0, 0))
