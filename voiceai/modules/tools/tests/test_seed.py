"""Seed invariants: curated internal rows, shaped and versioned (spec 0029)."""

from voiceai.modules.tools.constants import TOOLS_VERSION
from voiceai.modules.tools.seed import SEED_ENTRIES
from voiceai.modules.tools.static_methods import build_tool_id


def test_seed_rows_are_shaped_unique_and_current() -> None:
    """Natural keys match the built form; ids unique; version current; system stamped."""
    seen: set[str] = set()
    for entry in SEED_ENTRIES:
        assert entry.tool_id == build_tool_id(entry.kind, entry.name)
        assert entry.name, entry.tool_id
        assert entry.tools_version == TOOLS_VERSION
        assert entry.tenant_id == "system"
        assert entry.tool_id not in seen, f"duplicate seed row {entry.tool_id}"
        seen.add(entry.tool_id)


def test_seed_covers_the_internal_behaviors() -> None:
    """The implicit engine tools are explicit rows (hangup, transfer, knowledge)."""
    ids = {entry.tool_id for entry in SEED_ENTRIES}

    assert "internal:hangup" in ids
    assert "internal:transfer_call" in ids
    assert "internal:knowledge_search" in ids
