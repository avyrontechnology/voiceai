"""Tools mapping helpers: rows into wire shapes (AGENTS.md rule 1g)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.tools.models import ToolDefinition

__all__ = ["wire_tool", "wire_tool_list"]


def wire_tool(tool: ToolDefinition) -> dict[str, Any]:
    """Render one row as JSON (auth pointers included, never raw secrets).

    Args:
        tool: The stored row.

    Returns:
        JSON-able mapping of the row.
    """
    return tool.model_dump(mode="json")


def wire_tool_list(tools: list[ToolDefinition]) -> dict[str, Any]:
    """Render rows as a `{"tools": [...]}` payload.

    Args:
        tools: The stored rows.

    Returns:
        JSON-able mapping with the single `tools` key.
    """
    return {"tools": [wire_tool(tool) for tool in tools]}
