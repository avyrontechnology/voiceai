"""Guard helpers that raise the automation module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.automation.errors import GraphNotFoundError, GraphVersionNotFoundError
from voiceai.modules.automation.models import GraphDoc, GraphVersion


def ensure_graph_found(stored: GraphDoc | None, graph_id: str) -> GraphDoc:
    """Return the stored graph or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        graph_id: Requested graph id, echoed in the error details.

    Returns:
        The stored graph unchanged.

    Raises:
        GraphNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise GraphNotFoundError(graph_id)
    return stored


def ensure_version_found(stored: GraphVersion | None, version_number: int) -> GraphVersion:
    """Return the snapshot or raise the module's 404.

    Args:
        stored: Lookup result (``None`` when the version number was never issued).
        version_number: The version that was asked for.

    Returns:
        The stored snapshot unchanged.

    Raises:
        GraphVersionNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise GraphVersionNotFoundError(version_number)
    return stored
