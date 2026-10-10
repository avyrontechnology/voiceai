"""Webhooks module (spec 0076).

Owns the tenant's webhook registrations: which destinations exist, which agent
each belongs to, and what they subscribe to. Migrated out of the legacy
`platform` bucket, where the family was an opaque `{payload: ...}` row behind a
`MemoryStore`.

This module stores destinations; it never delivers to them. See CONTRACT.md for
why that distinction is load-bearing.
"""

from voiceai.modules import ModuleDef
from voiceai.modules.webhooks.constants import MODULE_NAME
from voiceai.modules.webhooks.controller import router
from voiceai.modules.webhooks.models import Webhook
from voiceai.modules.webhooks.repository import WebhookRepository
from voiceai.modules.webhooks.service import WebhooksService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-platform",
    slack_channel="#squad-platform",
    runbook_path="voiceai/modules/webhooks/RUNBOOK.md",
    max_lines=2000,
)

__all__ = ["MODULE", "Webhook", "WebhookRepository", "WebhooksService", "router"]
