# legacy-shim(spec-0002) — the legacy graph brain lives in voiceai.modules.agents.brains.legacy_graph (step A7).
"""Legacy import surface for the pre-preprocessed graph brain (import parity only).

Every name the old module bound stays importable from here: ``Node``, ``Graph`` and
``GraphBasedConversationAgent`` from their new home (exported, never constructed), and
the auxiliary imports re-created verbatim. Deleted at cutover — see the burn-down list
in specs/0002-agents-module.md §Rollout.
"""

import asyncio
import json
import random
import traceback

from voiceai.agent_types.base_agent import BaseAgent
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.utils import get_md5_hash, update_prompt_with_context

from voiceai.modules.agents.brains.legacy_graph import Graph, GraphBasedConversationAgent, Node, logger

__all__ = [
    "BaseAgent",
    "Graph",
    "GraphBasedConversationAgent",
    "Node",
    "configure_logger",
    "get_md5_hash",
    "logger",
    "update_prompt_with_context",
]
