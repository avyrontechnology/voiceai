# legacy-shim(spec-0002) — all seven brains live in voiceai.modules.agents.brains (steps A6/A7).
"""Legacy import surface for the agent brains.

``voiceai/agent_manager/task_manager.py`` star-imports this package (line 62), so the
exact legacy surface — the seven classes plus the submodule attributes — keeps resolving.
All brains re-export from ``voiceai.modules.agents.brains`` through their per-file shims:
the six easy brains since A6, and since A7 ``GraphAgent`` (split into ``brains/graph/*``)
and the never-constructed ``GraphBasedConversationAgent`` (``brains/legacy_graph.py``).
Deleted at cutover — see the burn-down list in specs/0002-agents-module.md §Rollout.
"""

from .contextual_conversational_agent import StreamingContextualAgent
from .extraction_agent import ExtractionContextualAgent
from .graph_based_conversational_agent import GraphBasedConversationAgent
from .summarization_agent import SummarizationContextualAgent
from .webhook_agent import WebhookAgent
from .graph_agent import GraphAgent
from .knowledgebase_agent import KnowledgeBaseAgent
