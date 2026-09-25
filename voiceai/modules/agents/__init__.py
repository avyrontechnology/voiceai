"""Agents module: the agent definition domain — schema, CRUD, prompts, and brains.

`__all__` here is the ONLY surface `voice` (spec 0004) may import from this package; the
layer-contract test enforces that mechanically (AGENTS.md §3.1, bridge 4).
"""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.agents.constants import MODULE_NAME
from voiceai.modules.agents.controller import router
from voiceai.modules.agents.errors import (
    AgentConfigInvalidError,
    AgentNotFoundError,
    AgentsError,
    PromptStoreError,
)
from voiceai.modules.agents.models.channel import Channel, Pipeline
from voiceai.modules.agents.models.pipeline import S2SConfig
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort, LlmPort
from voiceai.modules.agents.runtime import BrainFactory, BrainPort, CachedAgentReader, run_judgments
from voiceai.modules.agents.service import AgentService
from voiceai.modules.agents.static_methods import resolve_pipeline_for_task

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-agents",
    slack_channel="#squad-agents",
    runbook_path="voiceai/modules/agents/RUNBOOK.md",
    # Bumped for spec-0028 Phase A (channels, pipeline, PATCH) and spec-0029
    # slice 2 (tool refs + SSRF gate + tests); ratchets down after.
    max_lines=14700,
)

__all__ = [
    "MODULE",
    "AgentConfigInvalidError",
    "AgentDefinitionPort",
    "AgentNotFoundError",
    "AgentService",
    "AgentSessionStorePort",
    "AgentsError",
    "BrainFactory",
    "BrainPort",
    "CachedAgentReader",
    "Channel",
    "LlmPort",
    "Pipeline",
    "PromptStoreError",
    "S2SConfig",
    "resolve_pipeline_for_task",
    "run_judgments",
]
