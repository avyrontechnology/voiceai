"""Retention service: soft-erase rows past their collection TTL (spec 0073).

GDPR Art. 5(1)(e) storage limitation: data must not be kept longer than its
purpose allows. One pass per cycle soft-erases expired rows per collection;
erasure is a tombstone (`is_active=False`), never a destroy — the purge must
stay auditable (spec 0072 contract).

Consent records are excluded by construction: the registry never lists
`CONSENT_RECORDS`, and a unit test pins that a future edit adding it fails.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.modules.privacy import constants as C

__all__ = ["RetentionService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)

#: One purgeable collection: TTL seconds + the soft-erase call. The callable
#: receives the cutoff and returns how many rows it erased.
_PurgeTarget = tuple[int, Callable[[datetime], Awaitable[int]]]


class RetentionService:
    """Soft-erase expired rows per collection, one cycle at a time.

    Args:
        targets: Mapping of collection name → (TTL seconds, erase callable).
            TTL `0` disables that collection's purge.
    """

    def __init__(self, targets: Mapping[str, _PurgeTarget]) -> None:
        self._targets = dict(targets)

    async def run_cycle(self) -> dict[str, int]:
        """Run one retention pass over every registered collection.

        Per-collection exceptions are isolated: one bad target is logged and
        skipped, the rest of the cycle continues (AGENTS.md §5 — one bad item
        must not kill a consumer).

        Returns:
            Per-collection erased counts (collections that errored report 0).
        """
        cutoff = utc_now()
        results: dict[str, int] = {}
        for name, (ttl, erase) in self._targets.items():
            if ttl <= 0:
                results[name] = 0
                continue
            try:
                results[name] = await erase(cutoff)
            except Exception as exc:  # noqa: BLE001 — isolate per-iteration failures
                logger.error("retention purge failed collection=%s error=%s", name, type(exc).__name__)
                results[name] = 0
        return results
