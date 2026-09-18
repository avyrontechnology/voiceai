"""Voice module: the realtime call runtime — ports, session orchestration, providers.

Spec 0004 strangles the call runtime out of ``voiceai/agent_manager/task_manager.py``
into this package. Cross-module, this package imports from ``voiceai.modules.agents``
only names exported through its ``__init__.__all__`` (AGENTS.md §3.1, bridge 4), and
only files under ``adapters/`` may import legacy packages (bridge 1) — both enforced
mechanically by ``tests/arch/test_layer_contract.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from voiceai.common.logger import get_logger
from voiceai.modules import ModuleDef
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.controller import router
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
from voiceai.modules.voice.service import VoiceCallService

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container

#: Mounted by the app factory under the API prefix. Carries the flagged WS route
#: (dark unless ``Environment.voice_ws_enabled`` — spec 0004 B14); mounting it changes
#: nothing observable while the flag is off (the A1 precedent).


def register(container: Container) -> None:
    """Bind this module's providers into a container (AGENTS.md rule 9; spec 0004 B13a).

    B4 bound exactly `VoiceCallService` — the seam the quickstart WS handler resolves.
    B13a adds the prompt-store wiring: the service resolves `AgentSessionStorePort`
    (bound by the agents module; absent when agents is not composed) and prefetches
    prompt payloads through it, retiring the legacy prompt fetch. The adapter import
    lives inside the provider on purpose: building an app without ever resolving the
    service (every arch controller test) must not drag the legacy engine stack in.

    Args:
        container: The container being composed, already carrying the core
            infrastructure.
    """

    def build_voice_call_service(scope: Container) -> VoiceCallService:
        """Build the call service over the legacy-bridging adapters (§3.1 bridge 1)."""
        from voiceai.modules.agents import AgentSessionStorePort
        from voiceai.modules.voice.adapters.manager import build_assistant_manager, record_execution

        # The agents module binds the port when composed; absent, the legacy fetch stays.
        session_store = scope.resolve(AgentSessionStorePort) if scope.has(AgentSessionStorePort) else None
        return VoiceCallService(
            manager_factory=build_assistant_manager,
            execution_recorder=record_execution,
            logger=get_logger(MODULE_NAME),
            session_store=session_store,
        )

    container.register(VoiceCallService, build_voice_call_service)


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
    "VoiceCallService",
    "VoiceComponentError",
    "VoiceError",
    "WelcomeStateSetterPort",
    "register",
]
