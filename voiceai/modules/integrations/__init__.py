"""Integrations module (spec 0077).

Owns the tenant's third-party integrations: which providers are configured,
their credentials, and whether they are active. Migrated out of the legacy
`platform` bucket, where the family was an opaque `{payload: ...}` row behind a
`MemoryStore`.

This is the first migrated family that holds secrets. Its masking contract is
load-bearing — read CONTRACT.md before changing anything that touches `config`.
"""

from voiceai.modules import ModuleDef
from voiceai.modules.integrations.constants import MODULE_NAME
from voiceai.modules.integrations.controller import router
from voiceai.modules.integrations.models import Integration
from voiceai.modules.integrations.repository import IntegrationRepository
from voiceai.modules.integrations.service import IntegrationsService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/integrations/RUNBOOK.md",
    max_lines=2000,
)

__all__ = ["MODULE", "Integration", "IntegrationRepository", "IntegrationsService", "router"]
