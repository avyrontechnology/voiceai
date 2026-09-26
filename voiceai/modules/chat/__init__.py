"""Chat module: HTTP SSE text conversations over persisted per-agent history (spec 0038)."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.chat.constants import MODULE_NAME
from voiceai.modules.chat.controller import router
from voiceai.modules.chat.service import ChatService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/chat/RUNBOOK.md",
    max_lines=2000,  # Phase C closeout; ratchets down after.
)

__all__ = ["MODULE", "ChatService"]
