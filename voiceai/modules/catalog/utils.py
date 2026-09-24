"""Catalog impure utilities: the idempotent seed loader (AGENTS.md rule 1g).

Deploy-time entry point lives here (not in the service): inserting rows is
I/O, and the service stays orchestration over an already-seeded store.
"""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.modules.catalog.repository import CatalogRepository
from voiceai.modules.catalog.seed import seed_entries

__all__ = ["seed_catalog"]

logger: logging.Logger = get_logger("catalog")


async def seed_catalog(repository: CatalogRepository) -> int:
    """Load the curated seed rows idempotently (insert-or-replace by key).

    Args:
        repository: The `provider_catalog` collection (system-tenant view).

    Returns:
        The number of rows written.
    """
    count = 0
    for entry in seed_entries():
        await repository.save_entry(entry)
        count += 1
    logger.info("catalog seeded: %d rows", count)
    return count
