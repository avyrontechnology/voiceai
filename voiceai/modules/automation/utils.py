"""Module-internal impure utilities for the automation module (AGENTS.md rule 1g).

Pure versioning work lives in `static_methods.py`; this file holds the utilities
that touch process state rather than just data.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.automation import constants as C

__all__ = ["log_deploy", "log_restore"]


def log_restore(graph_id: str, version_number: int) -> None:
    """Record a graph restore.

    Logs the graph id and the version number only — a graph `definition` is
    customer content and is never dumped (AGENTS.md §4).

    Args:
        graph_id: Storage identifier of the graph.
        version_number: The snapshot that was restored.
    """
    get_logger(C.MODULE_NAME).info(C.LOG_RESTORED, graph_id, version_number)


def log_deploy(graph_id: str, agent_name: str) -> None:
    """Record a graph deployment.

    Args:
        graph_id: Storage identifier of the graph.
        agent_name: The name the generated agent was registered under.
    """
    get_logger(C.MODULE_NAME).info(C.LOG_DEPLOYED, graph_id, agent_name)
