"""Module-internal impure utilities of the voice module (AGENTS.md rule 1g).

Impure by definition — randomness and wall-clock reads live here so the session and
report code (steps B4-B7) never calls ``uuid``/``time`` ad hoc. Moved legacy bodies keep
their verbatim inline calls; only NEW code routes through these.
"""

from __future__ import annotations

import time
import uuid

__all__ = ["epoch_seconds", "new_mark_id"]


def new_mark_id() -> str:
    """Mint a fresh mark id for the mark ledger.

    The mark contract is uuid4 strings (the telephony output handlers mint one per
    pre/post mark message); keeping the mint here pins that scheme in one place.

    Returns:
        A random uuid4 in its canonical string form.
    """
    return str(uuid.uuid4())


def epoch_seconds() -> float:
    """Return the engine's wall clock: epoch seconds as a float.

    The realtime engine stamps everything (``sent_ts``, ``fired_at``, ``detected_at``)
    with ``time.time()`` floats — NOT datetimes, so `common.datetime_utils.utc_now` is
    the wrong tool at this seam. New voice code reads the clock through this one
    function so tests can patch a single lookup site.

    Returns:
        Current epoch time in seconds.
    """
    return time.time()
