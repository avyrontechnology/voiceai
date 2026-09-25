"""Tools module: shareable function + webhook tools (spec 0029)."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.tools.constants import MODULE_NAME
from voiceai.modules.tools.controller import router
from voiceai.modules.tools.service import ToolsService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/tools/RUNBOOK.md",
    max_lines=1500,
)

__all__ = ["MODULE", "ToolsService"]
