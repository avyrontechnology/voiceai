"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import (
    ToolDeniedError,
    ToolExecutionError,
    ToolForbiddenError,
    ToolNotFoundError,
)
from voiceai.modules.tools.models import ToolDefinition

__all__ = [
    "ensure_allowlisted",
    "ensure_arguments_valid",
    "ensure_endpoint_safe",
    "ensure_system_immutable",
    "ensure_tool_executable",
    "ensure_tool_found",
]


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


def ensure_tool_executable(tool: ToolDefinition | None, tool_id: str) -> ToolDefinition:
    """Return the row the executor may run, or raise 404 (spec 0056).

    Deprecated rows stay executable here (grandfathered, like reads — the
    attach gate in `get_tool_for_attach` owns deprecation, not the hot path).

    Args:
        tool: The looked-up row, or `None`.
        tool_id: The addressed canonical id (identifiers only, never payloads).

    Returns:
        The row, narrowed to non-`None`.

    Raises:
        ToolNotFoundError: When the row is missing (or foreign — no oracle).
    """
    if tool is None:
        raise ToolNotFoundError(f"Tool {tool_id!r} not found.", details={C.LOG_KEY_TOOL_ID: tool_id})
    return tool


def ensure_allowlisted(tool_id: str, agent_id: str, tool_refs: Sequence[str]) -> None:
    """Reject execution of a tool outside the agent's attached refs (spec 0056).

    Args:
        tool_id: The canonical executed id.
        agent_id: The owning agent (the allowlist principal).
        tool_refs: The snapshot's attached ref ids.

    Raises:
        ToolDeniedError: When `tool_id` is not attached (HTTP 403, identifiers
            only — arguments never enter the message or details).
    """
    if tool_id not in tool_refs:
        raise ToolDeniedError(
            C.ALLOWLIST_DENY_TEMPLATE.format(tool_id=tool_id, agent_id=agent_id),
            details={C.LOG_KEY_TOOL_ID: tool_id, C.LOG_KEY_AGENT_ID: agent_id},
        )


def ensure_arguments_valid(problems: Sequence[str], tool_id: str) -> None:
    """Reject calls whose arguments fail the row's JSON-Schema contract (spec 0056).

    Args:
        problems: The `validate_arguments` findings (schema paths only).
        tool_id: The canonical executed id.

    Raises:
        ToolExecutionError: When any problem was found (HTTP 400).
    """
    if problems:
        raise ToolExecutionError(
            f"Tool {tool_id!r} arguments failed validation.",
            details={C.LOG_KEY_TOOL_ID: tool_id, C.DETAIL_KEY_FIELDS: list(problems)},
        )


def ensure_endpoint_safe(is_safe: bool, tool_id: str) -> None:
    """Reject calls whose endpoint fails the SSRF pre-flight (spec 0056).

    Args:
        is_safe: The `is_safe_outbound_url` verdict for the endpoint.
        tool_id: The canonical executed id (the URL itself never leaves here —
            error opacity: logs and clients name the tool, never the address).

    Raises:
        ToolExecutionError: When the endpoint is unsafe (HTTP 400).
    """
    if not is_safe:
        raise ToolExecutionError(
            f"Tool {tool_id!r} endpoint failed safety validation.",
            details={C.LOG_KEY_TOOL_ID: tool_id},
        )
