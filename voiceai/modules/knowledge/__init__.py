"""Knowledge bases module (spec 0075).

Owns the tenant's knowledge bases: registration, source references, and which
agents each one is attached to. Migrated out of the legacy `platform` bucket,
where the family was an opaque `{payload: ...}` row behind a `MemoryStore`.
"""

from voiceai.modules import ModuleDef
from voiceai.modules.knowledge.constants import MODULE_NAME
from voiceai.modules.knowledge.controller import router
from voiceai.modules.knowledge.models import KBSource, KnowledgeBase
from voiceai.modules.knowledge.repository import KnowledgeBaseRepository
from voiceai.modules.knowledge.service import KnowledgeService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-agents",
    slack_channel="#squad-agents",
    runbook_path="voiceai/modules/knowledge/RUNBOOK.md",
    max_lines=2000,
)

__all__ = ["KBSource", "MODULE", "KnowledgeBase", "KnowledgeBaseRepository", "KnowledgeService", "router"]
