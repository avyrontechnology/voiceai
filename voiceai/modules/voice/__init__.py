"""Voice module: the realtime call runtime — ports, session orchestration, providers.

Spec 0004 strangles the call runtime out of ``voiceai/agent_manager/task_manager.py``
into this package. Cross-module, this package imports from ``voiceai.modules.agents``
only names exported through its ``__init__.__all__`` (AGENTS.md §3.1, bridge 4), and
only files under ``adapters/`` may import legacy packages (bridge 1) — both enforced
mechanically by ``tests/arch/test_layer_contract.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from voiceai.modules import ModuleDef
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.errors import (
    LlmError,
    S2SError,
    SynthesisError,
    TranscriptionError,
    UnknownComponentLabelError,
    VoiceComponentError,
    VoiceError,
)
from voiceai.modules.voice.ports import (
    ActiveTranscriberProbePort,
    AgentBrainPort,
    CallInputPort,
    CallOutputPort,
    GraphBrainPort,
    LlmPort,
    MarkLedgerPort,
    S2SPort,
    SequenceGatePort,
    SynthesisPoolPort,
    SynthesisPort,
    TranscriptionPoolPort,
    TranscriptionPort,
    WelcomeStateSetterPort,
)

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container

#: Mounted by the app factory under the API prefix. Empty on purpose: the flagged WS
#: controller lands in step B14, and mounting a router with no routes changes nothing
#: observable meanwhile (the A1 precedent).
router: APIRouter = APIRouter()


def register(container: Container) -> None:
    """Bind this module's providers into a container — a no-op until step B13a.

    B13a registers the adapters and the `AgentDefinitionPort` wiring here (AGENTS.md
    rule 9); the scaffold keeps the `ModuleDef` shape honest without wiring anything.

    Args:
        container: The container being composed; deliberately untouched for now.
    """


MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router, register=register)

__all__ = [
    "MODULE",
    "ActiveTranscriberProbePort",
    "AgentBrainPort",
    "CallInputPort",
    "CallOutputPort",
    "GraphBrainPort",
    "LlmError",
    "LlmPort",
    "MarkLedgerPort",
    "S2SError",
    "S2SPort",
    "SequenceGatePort",
    "SynthesisError",
    "SynthesisPoolPort",
    "SynthesisPort",
    "TranscriptionError",
    "TranscriptionPoolPort",
    "TranscriptionPort",
    "UnknownComponentLabelError",
    "VoiceComponentError",
    "VoiceError",
    "WelcomeStateSetterPort",
    "register",
]
