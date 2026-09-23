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
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort, LlmPort
from voiceai.modules.agents.service import AgentService

MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router)

__all__ = [
    "MODULE",
    "AgentConfigInvalidError",
    "AgentDefinitionPort",
    "AgentNotFoundError",
    "AgentService",
    "AgentSessionStorePort",
    "AgentsError",
    "LlmPort",
    "PromptStoreError",
]
