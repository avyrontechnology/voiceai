"""Knowledge module units: attach/detach idempotency and the legacy row read.

Attach idempotency and detach-while-unattached are the two behaviours the legacy
endpoints had that a careless rewrite would change, so they are pinned here.
"""

from __future__ import annotations

from voiceai.modules.knowledge.models import KBSource, KnowledgeBase
from voiceai.modules.knowledge.static_methods import attach, attached_to, detach


def _kb(name: str = "docs", agents: list[str] | None = None) -> KnowledgeBase:
    return KnowledgeBase(id="kb-1", name=name, agent_ids=list(agents or []))


def test_attach_adds_the_agent() -> None:
    """A first attach appends the agent."""
    kb = _kb()

    assert attach(kb, "agent-1") is True
    assert kb.agent_ids == ["agent-1"]


def test_attach_is_idempotent() -> None:
    """Attaching twice leaves one entry, matching the legacy behaviour."""
    kb = _kb()

    attach(kb, "agent-1")
    assert attach(kb, "agent-1") is False

    assert kb.agent_ids == ["agent-1"]


def test_attach_keeps_several_agents() -> None:
    """Each agent appears once, in attach order."""
    kb = _kb()

    attach(kb, "agent-1")
    attach(kb, "agent-2")

    assert kb.agent_ids == ["agent-1", "agent-2"]


def test_detach_removes_the_agent() -> None:
    """A detached agent is gone."""
    kb = _kb(agents=["agent-1", "agent-2"])

    assert detach(kb, "agent-1") is True

    assert kb.agent_ids == ["agent-2"]


def test_detach_removes_every_duplicate_occurrence() -> None:
    """A row that somehow lists the agent twice still ends with zero."""
    kb = _kb(agents=["agent-1", "agent-1", "agent-2"])

    assert detach(kb, "agent-1") is True

    assert kb.agent_ids == ["agent-2"]


def test_detach_of_an_unattached_agent_is_a_no_op() -> None:
    """The end state is what the caller asked for; it is not an error."""
    kb = _kb(agents=["agent-1"])

    assert detach(kb, "agent-9") is False

    assert kb.agent_ids == ["agent-1"]


def test_detach_on_an_empty_list_is_a_no_op() -> None:
    """Detaching from a knowledge base with no agents changes nothing."""
    kb = _kb()

    assert detach(kb, "agent-1") is False

    assert kb.agent_ids == []


def test_attached_to_filters_by_agent() -> None:
    """The agent's view lists only the knowledge bases it is attached to."""
    first = _kb("docs", agents=["agent-1"])
    second = _kb("policies", agents=["agent-2"])

    assert attached_to([first, second], "agent-1") == [first]
    assert attached_to([first, second], "agent-9") == []


def test_sources_carry_their_type_and_ref() -> None:
    """A source keeps the vocabulary it was created with."""
    kb = KnowledgeBase(name="docs", sources=[KBSource(type="url", ref="https://example.test/refunds")])

    assert kb.sources[0].type == "url"
    assert kb.sources[0].ref == "https://example.test/refunds"
