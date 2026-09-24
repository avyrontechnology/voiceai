"""Voice module: the realtime call runtime — ports, session orchestration, providers.

Spec 0004 strangles the call runtime out of ``voiceai/agent_manager/task_manager.py``
into this package. Cross-module, this package imports from ``voiceai.modules.agents``
only names exported through its ``__init__.__all__`` (AGENTS.md §3.1, bridge 4), and
only files under ``adapters/`` may import legacy packages (bridge 1) — both enforced
mechanically by ``tests/arch/test_layer_contract.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from voiceai.modules import ModuleDef
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.controller import router
from voiceai.modules.voice.errors import (
    LlmError,
    PlaceCallError,
    S2SError,
    SynthesisError,
    TalkoPartnerExistsError,
    TranscriptionError,
    UnknownComponentLabelError,
    UnknownTalkoPartnerError,
    VoiceComponentError,
    VoiceError,
)
from voiceai.modules.voice.models import (
    PlacedCall,
    TalkoPartnerConfig,
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
from voiceai.modules.voice.ports.outbound import DialOutcome, OutboundDialPort, PartnerPreview
from voiceai.modules.voice.repository import PlaceCallRepository, VoicePlaceCallRepository
from voiceai.modules.voice.schemas import VoiceContract
from voiceai.modules.voice.service import VoiceCallService

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    pass

#: Mounted by the app factory under the API prefix. Carries the flagged WS route
#: (dark unless ``Environment.voice_ws_enabled`` — spec 0004 B14); mounting it changes
#: nothing observable while the flag is off (the A1 precedent).


MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-voice",
    slack_channel="#squad-voice",
    runbook_path="voiceai/modules/voice/RUNBOOK.md",
    # Interim ceiling (spec 0019): M2/M3/M5 split this module; only ratchets down.
    max_lines=46000,
)

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
    "OutboundDialPort",
    "PartnerPreview",
    "S2SError",
    "S2SPort",
    "SequenceGatePort",
    "SynthesisError",
    "SynthesisPoolPort",
    "SynthesisPort",
    "DialOutcome",
    "PlaceCallError",
    "PlacedCall",
    "TalkoPartnerConfig",
    "TalkoPartnerExistsError",
    "TranscriptionError",
    "TranscriptionPoolPort",
    "TranscriptionPort",
    "PlaceCallRepository",
    "UnknownComponentLabelError",
    "UnknownTalkoPartnerError",
    "VoiceCallService",
    "VoiceContract",
    "VoicePlaceCallRepository",
    "VoiceComponentError",
    "VoiceError",
    "WelcomeStateSetterPort",
]
