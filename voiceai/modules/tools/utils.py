"""Tools impure utilities: the idempotent seed loader (AGENTS.md rule 1g)."""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.seed import seed_entries

__all__ = ["seed_tools"]

logger: logging.Logger = get_logger("tools")


async def seed_tools(repository: ToolsRepository) -> int:
    """Load the curated internal rows idempotently (insert-or-replace by key).

    Args:
        repository: The `tools` collection as a system-tenant view.

    Returns:
        The number of rows written.
    """
    count = 0
    for entry in seed_entries():
        await repository.save_tool(entry)
        count += 1
    logger.info("tools seeded: %d rows", count)
    return count
