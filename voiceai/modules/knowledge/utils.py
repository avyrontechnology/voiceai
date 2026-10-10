"""Module-internal impure utilities for the knowledge module (AGENTS.md rule 1g).

Pure list work lives in `static_methods.py`; this file holds the utilities that
touch process state rather than just data.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.knowledge import constants as C

__all__ = ["log_orphaned_attachment"]


def log_orphaned_attachment(kb_id: str, agent_id: str) -> None:
    """Record an attach whose agent no longer resolves to a live agent.

    Sources may be customer data, so only identifiers are logged.

    Args:
        kb_id: Storage identifier of the knowledge base.
        agent_id: The agent that was attached.
    """
    get_logger(C.MODULE_NAME).warning(C.LOG_ATTACHED, kb_id, agent_id)
