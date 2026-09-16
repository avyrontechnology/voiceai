"""Time helpers: everything aware, everything UTC, nothing naive."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from voiceai.common.datetime_utils import IST, UTC, epoch_ms, isoformat_z, parse_iso, to_ist, utc_now
from voiceai.common.errors import InvalidRequestError

FIXED_UTC = datetime(2026, 9, 16, 10, 30, 0, tzinfo=UTC)


class TestUtcNow:
    """The project's only clock read."""

    def test_is_timezone_aware_utc(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_advances(self) -> None:
        assert utc_now() <= utc_now()


class TestIsoformatZ:
    """The wire format clients parse."""

    def test_renders_utc_with_a_z_suffix(self) -> None:
        assert isoformat_z(FIXED_UTC) == "2026-09-16T10:30:00Z"

    def test_naive_input_is_treated_as_utc(self) -> None:
        assert isoformat_z(datetime(2026, 9, 16, 10, 30, 0)) == "2026-09-16T10:30:00Z"

    def test_other_zones_are_converted_before_rendering(self) -> None:
        assert isoformat_z(FIXED_UTC.astimezone(IST)) == "2026-09-16T10:30:00Z"


class TestParseIso:
    """Parsing is a boundary: bad input becomes an `AppError`, never a `ValueError`."""

    def test_round_trips_with_isoformat_z(self) -> None:
        assert parse_iso(isoformat_z(FIXED_UTC)) == FIXED_UTC

    def test_accepts_an_explicit_offset(self) -> None:
        assert parse_iso("2026-09-16T16:00:00+05:30") == FIXED_UTC

    def test_offsetless_input_is_read_as_utc(self) -> None:
        assert parse_iso("2026-09-16T10:30:00") == FIXED_UTC

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        assert parse_iso("  2026-09-16T10:30:00Z  ") == FIXED_UTC

    def test_garbage_raises_an_app_error(self) -> None:
        with pytest.raises(InvalidRequestError) as raised:
            parse_iso("not-a-timestamp")
        assert isinstance(raised.value.__cause__, ValueError)


class TestToIst:
    """Presentation-only conversion; storage stays UTC."""

    def test_shifts_by_five_hours_thirty(self) -> None:
        converted = to_ist(FIXED_UTC)
        assert converted.utcoffset() == timedelta(hours=5, minutes=30)
        assert (converted.hour, converted.minute) == (16, 0)

    def test_same_instant_is_preserved(self) -> None:
        assert to_ist(FIXED_UTC) == FIXED_UTC

    def test_naive_input_is_treated_as_utc(self) -> None:
        assert to_ist(datetime(2026, 9, 16, 10, 30, 0)).hour == 16


class TestEpochMs:
    """Milliseconds for TTLs and metrics."""

    def test_converts_a_known_instant(self) -> None:
        assert epoch_ms(datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)) == 1000

    def test_defaults_to_now(self) -> None:
        before = epoch_ms(utc_now())
        assert epoch_ms() >= before

    def test_respects_the_source_timezone(self) -> None:
        other_zone = FIXED_UTC.astimezone(timezone(timedelta(hours=-5)))
        assert epoch_ms(other_zone) == epoch_ms(FIXED_UTC)
