"""Curated internal tool seed (spec 0029, slice 1).

Curation rules (review to extend — never scrape): the rows name the engine's
implicit tools (end-call injection, transfer webhook, knowledge retrieval)
with descriptions/parameters documented from engine behavior. Tenant rows are
never seeded — tenants author their own.
"""

from __future__ import annotations

from typing import TypedDict

from voiceai.modules.tools.constants import (
    TOOL_KIND_INTERNAL,
    TOOL_KIND_WEBHOOK,
    TOOLS_VERSION,
)
from voiceai.modules.tools.models import ToolDefinition

__all__ = ["SEED_ENTRIES", "seed_entries"]


class _RowOptions(TypedDict, total=False):
    """Seed-row options beyond the (kind, name) key."""

    description: str
    parameters: dict
    url: str | None
    method: str
    timeout_s: int
    deprecated: bool


def _entry(kind: str, name: str, options: _RowOptions) -> ToolDefinition:
    """Build one tool row with the v1 seed version stamped."""
    return ToolDefinition(
        tool_id=f"{kind}:{name}",
        kind=kind,  # type: ignore[arg-type]  # why: table literals, census-pinned like the catalog seed
        name=name,
        description=str(options.get("description", "")),
        parameters=dict(options.get("parameters", {})),
        url=options.get("url"),
        method=str(options.get("method", "POST")),
        timeout_s=int(options.get("timeout_s", 10)),
        deprecated=bool(options.get("deprecated", False)),
        tools_version=TOOLS_VERSION,
    )


#: The curated v1 rows: (kind, name, options).
_SEED_TABLE: tuple[tuple[str, str, _RowOptions], ...] = (
    (
        TOOL_KIND_INTERNAL,
        "hangup",
        {
            "description": "End the call immediately (engine end-call injection).",
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    (
        TOOL_KIND_INTERNAL,
        "transfer_call",
        {
            "description": "Transfer the live call to another destination or agent.",
            "parameters": {
                "type": "object",
                "properties": {"destination": {"type": "string"}},
                "required": ["destination"],
            },
        },
    ),
    (
        TOOL_KIND_INTERNAL,
        "knowledge_search",
        {
            "description": "Retrieve knowledge-base context before responding.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ),
    (
        TOOL_KIND_WEBHOOK,
        "pre_call_notify",
        {
            "description": "Fire-and-forget notification before a tool's main request.",
            "parameters": {
                "type": "object",
                "properties": {"event": {"type": "string"}},
            },
            # spec-0046 Slice A decision (loud): this row ships with NO endpoint,
            # so it is attachable-but-broken (attaches 400 "has no endpoint"). No
            # real platform endpoint exists and new endpoints are non-goals, so a
            # hardcoded URL would pass attach and fail at runtime — worse than
            # failing loud at attach. Unseeding now would drop the curated name
            # and break the pinned seed count (that unseed is the stage-2
            # deleting spec's job); instead the row seeds DEPRECATED: new
            # attaches 400 naming the tool (ToolsService.get_tool_for_attach),
            # pickers filter it, already-attached agents keep resolving until
            # stage 2. Tenants may still curate their own working
            # `webhook:pre_call_notify` row (tenant rows win ties).
            "deprecated": True,
        },
    ),
)


#: Materialized seed rows (built once; the loader inserts them idempotently).
SEED_ENTRIES: tuple[ToolDefinition, ...] = tuple(
    _entry(kind, name, options) for kind, name, options in _SEED_TABLE
)


def seed_entries() -> list[ToolDefinition]:
    """Return fresh copies of the seed rows for the loader."""
    return [entry.model_copy(deep=True) for entry in SEED_ENTRIES]
