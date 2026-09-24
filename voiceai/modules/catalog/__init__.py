"""Catalog module: provider/model options for the agent builder (spec 0022)."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.catalog.constants import MODULE_NAME
from voiceai.modules.catalog.controller import router
from voiceai.modules.catalog.service import CatalogService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/catalog/RUNBOOK.md",
    max_lines=1500,
)

__all__ = ["MODULE", "CatalogService"]
