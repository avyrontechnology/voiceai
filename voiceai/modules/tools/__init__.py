"""Tools module: shareable function + webhook tools (spec 0029)."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.tools.constants import MODULE_NAME
from voiceai.modules.tools.controller import router
from voiceai.modules.tools.dcr_service import ClientRecord, DcrService
from voiceai.modules.tools.executor import ToolExecutionResult, ToolExecutor
from voiceai.modules.tools.service import ToolsService
from voiceai.modules.tools.vault import CredentialVault
MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/tools/RUNBOOK.md",
    # spec 0049: route-gate + 422 tests pushed the total past 1500; ratcheted minimally.
    # spec 0056: executor + vault + MCP execution lane (5000 ceiling; ratchet down next slice).
    # spec 0057: MCP server pilot (server router + controller route + tests).
    max_lines=5400,
)

__all__ = [
    "MODULE",
    "ClientRecord",
    "CredentialVault",
    "DcrService",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolsService",
]
