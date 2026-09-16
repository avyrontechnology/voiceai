"""Agents module: the agent definition domain — schema, CRUD, prompts, and brains.

`__all__` here is the ONLY surface `voice` (spec 0004) may import from this package; the
layer-contract test enforces that mechanically (AGENTS.md §3.1, bridge 4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from voiceai.modules import ModuleDef
from voiceai.modules.agents.constants import MODULE_NAME
from voiceai.modules.agents.errors import (
    AgentConfigInvalidError,
    AgentNotFoundError,
    AgentsError,
    PromptStoreError,
)
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container

#: Mounted by the app factory under the API prefix. Empty on purpose: the controller lands
#: in step A4, and mounting a router with no routes changes nothing observable meanwhile.
router: APIRouter = APIRouter()


def register(container: Container) -> None:
    """Bind this module's providers into a container — a no-op until step A4.

    A4 registers the repository, the service, and the port bindings here (AGENTS.md
    rule 9); the scaffold keeps the `ModuleDef` shape honest without wiring anything.

    Args:
        container: The container being composed; deliberately untouched for now.
    """


MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router, register=register)

__all__ = [
    "MODULE",
    "AgentConfigInvalidError",
    "AgentDefinitionPort",
    "AgentNotFoundError",
    "AgentSessionStorePort",
    "AgentsError",
    "PromptStoreError",
    "register",
]
