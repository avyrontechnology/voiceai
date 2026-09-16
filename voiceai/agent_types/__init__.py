# legacy-shim(spec-0002) — the six easy brains live in voiceai.modules.agents.brains (step A6).
"""Legacy import surface for the agent brains.

``voiceai/agent_manager/task_manager.py`` star-imports this package (line 62), so the
exact legacy surface — the seven classes plus the submodule attributes — keeps resolving.
The six easy brains re-export from ``voiceai.modules.agents.brains`` through their
per-file shims; the graph agents stay at their legacy paths until step A7 splits them.
Deleted at cutover — see the burn-down list in specs/0002-agents-module.md §Rollout.
"""

from .contextual_conversational_agent import StreamingContextualAgent
from .extraction_agent import ExtractionContextualAgent
from .graph_based_conversational_agent import GraphBasedConversationAgent
from .summarization_agent import SummarizationContextualAgent
from .webhook_agent import WebhookAgent
from .graph_agent import GraphAgent
from .knowledgebase_agent import KnowledgeBaseAgent
