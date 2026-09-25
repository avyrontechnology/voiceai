"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.tools.errors import ToolForbiddenError, ToolNotFoundError

__all__ = ["ensure_system_immutable", "ensure_tool_found"]


def ensure_tool_found(value: Any | None, tool_id: str) -> Any:
    """Return the tool row, or raise 404 (foreign rows arrive as `None`).

    Args:
        value: The looked-up row, or `None`.
        tool_id: The addressed id (identifiers only, never payloads).

    Returns:
        The row, narrowed to non-`None`.

    Raises:
        ToolNotFoundError: When the row is missing (or foreign — no oracle).
    """
    if value is None:
        raise ToolNotFoundError(f"Tool {tool_id!r} not found.", details={"tool_id": tool_id})
    return value


def ensure_system_immutable(is_system_row: bool, tool_id: str) -> None:
    """Reject writes addressing system rows (spec 0029).

    Args:
        is_system_row: Whether the addressed row is system-owned.
        tool_id: The addressed id.

    Raises:
        ToolForbiddenError: When a tenant write targets a system row.
    """
    if is_system_row:
        raise ToolForbiddenError(
            f"System tool {tool_id!r} is read-only; curation happens in code review.",
            details={"tool_id": tool_id},
        )
