"""The agent brains: the runtime conversation/judgment classes the engine composes.

Step A6 landed the six easy brains; step A7 completes the 7-brain registry with
``GraphAgent`` (the 1,501-line ``voiceai/agent_types/graph_agent.py`` split into
``brains/graph/*``, which also re-exports ``_DETERMINISTIC_REASONING_PREFIX``) and the
``GraphBasedConversationAgent`` import-parity re-export from ``brains/legacy_graph.py``
(exported, never constructed). Every ``voiceai/agent_types/`` path is now a
``# legacy-shim(spec-0002)`` re-export of this package.
"""

from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.brains.extraction import ExtractionContextualAgent
from voiceai.modules.agents.brains.graph import GraphAgent
from voiceai.modules.agents.brains.knowledgebase import KnowledgeBaseAgent
from voiceai.modules.agents.brains.legacy_graph import GraphBasedConversationAgent
from voiceai.modules.agents.brains.simple import StreamingContextualAgent
from voiceai.modules.agents.brains.summarization import SummarizationContextualAgent
from voiceai.modules.agents.brains.webhook import WebhookAgent

__all__ = [
    "BaseAgent",
    "ExtractionContextualAgent",
    "GraphAgent",
    "GraphBasedConversationAgent",
    "KnowledgeBaseAgent",
    "StreamingContextualAgent",
    "SummarizationContextualAgent",
    "WebhookAgent",
]
