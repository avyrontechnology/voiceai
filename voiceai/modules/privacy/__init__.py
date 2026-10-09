"""Privacy module: GDPR consent records (spec 0071)."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.privacy.constants import MODULE_NAME
from voiceai.modules.privacy.controller import router
from voiceai.modules.privacy.service import PrivacyService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/privacy/RUNBOOK.md",
    max_lines=3000,
)

__all__ = ["MODULE", "PrivacyService"]
