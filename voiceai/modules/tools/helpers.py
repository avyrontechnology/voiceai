"""Tools mapping helpers: rows into wire shapes (AGENTS.md rule 1g)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from voiceai.modules.tools.models import ToolDefinition

__all__ = ["validation_field_paths", "wire_tool", "wire_tool_list"]


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


def validation_field_paths(exc: ValidationError) -> list[str]:
    """Reduce a validation failure to its dotted field paths (spec 0049, hardened spec 0052).

    Locations are client-safe (they name the schema, not the input); the
    messages are not (pydantic echoes the offending value), so only the
    locations leave this function. A record without a `loc` sequence contributes
    nothing instead of raising `KeyError`, so a malformed record can never turn
    a 400 into a 500.

    Args:
        exc: The pydantic failure.

    Returns:
        Sorted, de-duplicated dotted paths such as `["kind", "parameters.x"]`.
    """
    paths: set[str] = set()
    for error in exc.errors():
        raw_loc: Any = error.get("loc", ())
        loc: tuple[Any, ...] | list[Any] = raw_loc if isinstance(raw_loc, (list, tuple)) else ()
        if loc:
            paths.add(".".join(str(part) for part in loc))
    return sorted(paths)
