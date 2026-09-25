"""Channel + pipeline vocabulary for agent architecture (spec 0028, Phase A).

Strict literals only: `channels` names the runtimes an agent serves, `pipeline`
selects the active engine path per conversation task. No migration — missing
keys materialize through model defaults.
"""

from __future__ import annotations

from typing import Literal

from voiceai.modules.agents.constants import (
    CHANNEL_CHAT,
    CHANNEL_VOICE,
    PIPELINE_ASR,
    PIPELINE_CHAT,
    PIPELINE_S2S,
    WRITABLE_CHANNELS,
)

__all__ = [
    "CHANNEL_CHAT",
    "CHANNEL_VOICE",
    "PIPELINE_ASR",
    "PIPELINE_CHAT",
    "PIPELINE_S2S",
    "WRITABLE_CHANNELS",
    "Channel",
    "Pipeline",
]

#: Runtimes an agent may serve. Phase A writes voice only (chat rejects until
#: Phase C delivers its runtime — no dormant data).
Channel = Literal["voice", "chat"]

#: Engine path per conversation task. `None` on a task infers legacy behavior.
Pipeline = Literal["asr", "s2s", "chat"]
