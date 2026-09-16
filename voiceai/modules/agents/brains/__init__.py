"""The agent brains: the runtime conversation/judgment classes the engine composes.

Step A6 lands the six easy brains; step A7 completes the 7-brain registry with the two
placeholders documented here:

- ``GraphAgent`` — arrives when A7 splits the 1,501-line ``voiceai/agent_types/graph_agent.py``
  into ``brains/graph/*`` (it re-exports ``_DETERMINISTIC_REASONING_PREFIX`` as well);
- ``GraphBasedConversationAgent`` — the legacy_graph import-parity re-export (exported,
  never constructed), still at ``voiceai/agent_types/graph_based_conversational_agent.py``
  until A7.

Until then the legacy ``voiceai.agent_types`` package (now a shim) remains the only path
that resolves all seven names.
"""

from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.brains.extraction import ExtractionContextualAgent
from voiceai.modules.agents.brains.knowledgebase import KnowledgeBaseAgent
from voiceai.modules.agents.brains.simple import StreamingContextualAgent
from voiceai.modules.agents.brains.summarization import SummarizationContextualAgent
from voiceai.modules.agents.brains.webhook import WebhookAgent

__all__ = [
    "BaseAgent",
    "ExtractionContextualAgent",
    "KnowledgeBaseAgent",
    "StreamingContextualAgent",
    "SummarizationContextualAgent",
    "WebhookAgent",
]
