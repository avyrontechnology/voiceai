"""Clock-reading utilities: uptime clamping and probe timing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter

from voiceai.common.datetime_utils import utc_now
from voiceai.modules.health.constants import ROUND_DECIMALS
from voiceai.modules.health.utils import elapsed_ms, uptime_seconds

STARTED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_uptime_seconds_measures_from_the_explicit_reference() -> None:
    """With `now` supplied, the result is exactly the difference in seconds."""
    assert uptime_seconds(STARTED_AT, now=STARTED_AT + timedelta(seconds=90)) == 90.0


def test_uptime_seconds_rounds_to_the_payload_precision() -> None:
    """Sub-millisecond noise is rounded away, keeping the payload readable."""
    now = STARTED_AT + timedelta(seconds=1, microseconds=123_456)

    assert uptime_seconds(STARTED_AT, now=now) == 1.123


def test_uptime_seconds_clamps_a_backwards_clock_to_zero() -> None:
    """A reference before the start reads as zero uptime, never a negative number."""
    assert uptime_seconds(STARTED_AT, now=STARTED_AT - timedelta(seconds=30)) == 0.0


def test_uptime_seconds_defaults_to_the_current_clock() -> None:
    """Without `now`, the wall clock is read: a past start yields a positive uptime."""
    assert uptime_seconds(utc_now() - timedelta(seconds=5)) > 0.0


def test_elapsed_ms_measures_since_the_perf_counter_reading() -> None:
    """A reading taken in the past yields at least that much elapsed time, in milliseconds."""
    started = perf_counter() - 0.05

    assert elapsed_ms(started) >= 50.0


def test_elapsed_ms_rounds_to_the_payload_precision() -> None:
    """The value is non-negative and carries at most `ROUND_DECIMALS` decimals."""
    elapsed = elapsed_ms(perf_counter())

    assert elapsed >= 0.0
    assert elapsed == round(elapsed, ROUND_DECIMALS)
