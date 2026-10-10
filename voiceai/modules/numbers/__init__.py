"""Phone numbers module (spec 0074).

Owns the tenant's dialled-in numbers: registration, agent assignment, and the
Talko inbound resolve lookup. Migrated out of the legacy `platform` bucket, where
the family lived as an opaque `{payload: ...}` row behind a `MemoryStore`.
"""

from voiceai.modules import ModuleDef
from voiceai.modules.numbers.constants import MODULE_NAME
from voiceai.modules.numbers.controller import router
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.repository import PhoneNumberRepository
from voiceai.modules.numbers.service import PhoneNumbersService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-voice",
    slack_channel="#squad-voice",
    runbook_path="voiceai/modules/numbers/RUNBOOK.md",
    max_lines=2000,
)

__all__ = ["MODULE", "PhoneNumber", "PhoneNumberRepository", "PhoneNumbersService", "router"]
