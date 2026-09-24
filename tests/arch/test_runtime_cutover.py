"""Runtime-cutover tripwire: the legacy brain assembly stays deleted (spec 0024, M3).

Scans the legacy file as text (no import — robust while peers edit around
it): the verbatim graph/knowledgebase assembly markers must never return to
`TaskManager.__get_agent_object`. The behavioral pin lives in
`tests/test_brain_factory_equivalence.py`; this is the mechanical backstop.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_MANAGER = REPO_ROOT / "voiceai" / "agent_manager" / "task_manager.py"

#: Markers of the deleted verbatim branches. Any return fails loudly.
RETIRED_MARKERS: frozenset[str] = frozenset(
    {
        "StreamingContextualAgent(",
        "GraphAgent(",
        "KnowledgeBaseAgent(",
        "Agent type is not created yet",
    }
)


def _method_source() -> str:
    """Return the `__get_agent_object` method body as text."""
    text = TASK_MANAGER.read_text(encoding="utf-8")
    start = text.index("def __get_agent_object")
    end = text.index("def __setup_s2s", start)
    return text[start:end]


def test_verbatim_assembly_markers_are_gone() -> None:
    """The deleted branches cannot silently return to the legacy method."""
    source = _method_source()
    for marker in sorted(RETIRED_MARKERS):
        assert marker not in source, f"verbatim marker returned: {marker}"


def test_factory_seam_is_the_only_path() -> None:
    """The method resolves through the injected factory, failing fast without it."""
    source = _method_source()
    assert 'self.kwargs.get("brain_factory")' in source
    assert "spec 0024" in source
