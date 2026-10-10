"""Automation module (spec 0078).

Owns graphs: their records, their snapshot history, and the engine that
validates, dry-runs and deploys them. Migrated out of the legacy `platform`
bucket, where the records were a `{payload: ...}` row behind a `MemoryStore` and
the engine lived in `voiceai/platform/graphs.py`.

**Records and engine moved together, deliberately.** An earlier attempt moved
the records first and left the engine routes on the legacy store, which left two
readers pointing at two different stores for the same collection — a graph
created through one route was invisible to the other. See CONTRACT.md.
"""

from voiceai.modules import ModuleDef
from voiceai.modules.automation.constants import MODULE_NAME
from voiceai.modules.automation.controller import router
from voiceai.modules.automation.engine import (
    DryRunResult,
    GraphDefinition,
    ValidationResult,
    dry_run,
    graph_to_agent_payload,
    validate_definition,
)
from voiceai.modules.automation.models import GraphDoc, GraphVersion
from voiceai.modules.automation.repository import GraphRepository
from voiceai.modules.automation.service import GraphService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-agents",
    slack_channel="#squad-agents",
    runbook_path="voiceai/modules/automation/RUNBOOK.md",
    max_lines=3000,
)

__all__ = [
    "MODULE",
    "DryRunResult",
    "GraphDefinition",
    "GraphDoc",
    "GraphRepository",
    "GraphService",
    "GraphVersion",
    "ValidationResult",
    "dry_run",
    "graph_to_agent_payload",
    "router",
    "validate_definition",
]
