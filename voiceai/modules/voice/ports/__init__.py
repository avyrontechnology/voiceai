"""The hexagonal seam of the voice module: ports the call runtime consumes (spec 0004).

Every port is a `runtime_checkable` structural `Protocol`: legacy classes conform
WITHOUT importing ``voiceai.modules.*``, this package imports no legacy code (§3.1 —
only ``adapters/`` may, and the conformance tests live in ``tests/``), and payloads
stay plain dicts because the census-verified engine seam is dict-shaped.

One import surface on purpose: adapters, session code, and tests import ports from
here, so a port moving between files can never ripple.
"""

from __future__ import annotations

from voiceai.modules.voice.ports.llm import AgentBrainPort, GraphBrainPort, LlmPort
from voiceai.modules.voice.ports.s2s import S2SPort
from voiceai.modules.voice.ports.synthesis import SequenceGatePort, SynthesisPoolPort, SynthesisPort
from voiceai.modules.voice.ports.telephony import (
    CallInputPort,
    CallOutputPort,
    MarkLedgerPort,
    WelcomeStateSetterPort,
)
from voiceai.modules.voice.ports.transcription import (
    ActiveTranscriberProbePort,
    TranscriptionPoolPort,
    TranscriptionPort,
)

__all__ = [
    "ActiveTranscriberProbePort",
    "AgentBrainPort",
    "CallInputPort",
    "CallOutputPort",
    "GraphBrainPort",
    "LlmPort",
    "MarkLedgerPort",
    "S2SPort",
    "SequenceGatePort",
    "SynthesisPoolPort",
    "SynthesisPort",
    "TranscriptionPoolPort",
    "TranscriptionPort",
    "WelcomeStateSetterPort",
]
