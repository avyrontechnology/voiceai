"""Characterization: `BaseAgent` pins (spec 0002, step A6).

The six easy brains had zero tests; these pin the legacy surface BEFORE the move, through
the legacy import path, so the same suite proves parity after `voiceai/agent_types/*`
become shims.
"""

from __future__ import annotations

from voiceai.agent_types.base_agent import BaseAgent
from voiceai.agent_types.contextual_conversational_agent import StreamingContextualAgent
from voiceai.agent_types.extraction_agent import ExtractionContextualAgent
from voiceai.agent_types.knowledgebase_agent import KnowledgeBaseAgent
from voiceai.agent_types.summarization_agent import SummarizationContextualAgent
from voiceai.agent_types.webhook_agent import WebhookAgent

LEGACY_BASE_AGENT_NAME = "base-agent"


def test_base_agent_carries_the_shared_name_tag():
    """Every brain inherits `agent_name == "base-agent"` from the base constructor."""
    assert BaseAgent().agent_name == LEGACY_BASE_AGENT_NAME


def test_the_five_easy_brains_subclass_base_agent():
    """The brain hierarchy roots at `BaseAgent` (the engine relies on the shared tag)."""
    for brain in (
        StreamingContextualAgent,
        ExtractionContextualAgent,
        KnowledgeBaseAgent,
        SummarizationContextualAgent,
        WebhookAgent,
    ):
        assert issubclass(brain, BaseAgent)
