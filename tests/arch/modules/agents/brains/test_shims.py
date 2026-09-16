"""Shim and surface pins for the six moved brains (spec 0002, step A6).

The characterization suites in this directory import through the LEGACY paths, so they
prove behavior parity by construction; these tests pin the mechanics of the move itself:
the legacy paths answer the SAME class objects (stronger than equal behavior), the
package star-import surface `task_manager.py:62` consumes is a superset of the pre-move
snapshot, the shim files carry their tag, and the graph agents are untouched until A7.
"""

from __future__ import annotations

from pathlib import Path

import voiceai.agent_types
from voiceai.modules.agents import brains

#: `sorted(n for n in vars(voiceai.agent_types) if not n.startswith("_"))`, recorded on
#: the pre-move tree (2026-09-17): the seven classes plus the submodule attributes.
PRE_MOVE_STAR_SURFACE = frozenset(
    {
        "ExtractionContextualAgent",
        "GraphAgent",
        "GraphBasedConversationAgent",
        "KnowledgeBaseAgent",
        "StreamingContextualAgent",
        "SummarizationContextualAgent",
        "WebhookAgent",
        "base_agent",
        "contextual_conversational_agent",
        "extraction_agent",
        "graph_agent",
        "graph_based_conversational_agent",
        "knowledgebase_agent",
        "summarization_agent",
        "webhook_agent",
    }
)

SHIM_TAG = "# legacy-shim(spec-0002"
SHIMMED_FILES = (
    "__init__.py",
    "base_agent.py",
    "contextual_conversational_agent.py",
    "extraction_agent.py",
    "knowledgebase_agent.py",
    "summarization_agent.py",
    "webhook_agent.py",
)
A7_FILES = ("graph_agent.py", "graph_based_conversational_agent.py")

#: legacy module name → (brains submodule name, class name)
MOVES = {
    "base_agent": ("base", "BaseAgent"),
    "contextual_conversational_agent": ("simple", "StreamingContextualAgent"),
    "extraction_agent": ("extraction", "ExtractionContextualAgent"),
    "knowledgebase_agent": ("knowledgebase", "KnowledgeBaseAgent"),
    "summarization_agent": ("summarization", "SummarizationContextualAgent"),
    "webhook_agent": ("webhook", "WebhookAgent"),
}


def test_package_star_surface_is_a_superset_of_the_pre_move_snapshot():
    """`from voiceai.agent_types import *` (task_manager.py:62) loses no name."""
    public = {name for name in vars(voiceai.agent_types) if not name.startswith("_")}
    assert PRE_MOVE_STAR_SURFACE <= public


def test_legacy_paths_answer_the_same_class_objects():
    """Import parity, stronger than equal behavior: `is`, per module and per package."""
    for legacy_name, (new_name, class_name) in MOVES.items():
        legacy_module = getattr(voiceai.agent_types, legacy_name)
        new_module = getattr(brains, new_name)
        assert getattr(legacy_module, class_name) is getattr(new_module, class_name)
    for class_name in ("StreamingContextualAgent", "KnowledgeBaseAgent", "WebhookAgent"):
        assert getattr(voiceai.agent_types, class_name) is getattr(brains, class_name)


def test_brains_package_exports_exactly_the_six():
    """A7 adds the graph placeholders; until then `__all__` is exactly the six."""
    assert set(brains.__all__) == {class_name for _, class_name in MOVES.values()}
    for name in brains.__all__:
        assert getattr(brains, name) is not None


def test_moved_classes_report_their_new_modules():
    """`__module__` seams (used by dynamic patch targets) point at the new homes."""
    for _, (new_name, class_name) in MOVES.items():
        brain_class = getattr(brains, class_name)
        assert brain_class.__module__ == f"voiceai.modules.agents.brains.{new_name}"


def test_shim_files_carry_the_tag_and_graph_files_do_not():
    """The seven shims are tagged in their first lines; the A7 files stay untouched."""
    package_dir = Path(voiceai.agent_types.__file__).parent
    for file_name in SHIMMED_FILES:
        head = (package_dir / file_name).read_text(encoding="utf-8").splitlines()[:3]
        assert any(SHIM_TAG in line for line in head), file_name
    for file_name in A7_FILES:
        head = (package_dir / file_name).read_text(encoding="utf-8").splitlines()[:3]
        assert not any(SHIM_TAG in line for line in head), file_name
