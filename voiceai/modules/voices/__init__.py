"""Voices module: per-agent custom voice library (spec 0025)."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.voices.constants import MODULE_NAME
from voiceai.modules.voices.controller import router
from voiceai.modules.voices.service import VoicesService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/voices/RUNBOOK.md",
    max_lines=1500,
)

__all__ = ["MODULE", "VoicesService"]
