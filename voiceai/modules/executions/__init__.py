"""Executions module (spec 0080).

Owns call records, their stats and latency readouts, and the simulated-call
runner. Migrated out of the legacy `platform` bucket.

Moved **ahead of workflows** (spec 0079) on purpose: the workflow engine's
`agent` node calls `run_simulated_call`, so migrating it here is what lets that
engine import a module instead of reaching into `platform`.
"""

from voiceai.modules import ModuleDef
from voiceai.modules.executions.constants import MODULE_NAME
from voiceai.modules.executions.controller import calls_router, executions_router, router
from voiceai.modules.executions.models import (
    Execution,
    ExecutionStats,
    ExecutionStatus,
    LatencyStats,
    TranscriptTurn,
)
from voiceai.modules.executions.repository import ExecutionRepository
from voiceai.modules.executions.runner import run_simulated_call
from voiceai.modules.executions.service import ExecutionsService

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-voice",
    slack_channel="#squad-voice",
    runbook_path="voiceai/modules/executions/RUNBOOK.md",
    max_lines=2500,
)

__all__ = [
    "MODULE",
    "Execution",
    "ExecutionRepository",
    "ExecutionStats",
    "ExecutionStatus",
    "ExecutionsService",
    "LatencyStats",
    "TranscriptTurn",
    "calls_router",
    "executions_router",
    "router",
    "run_simulated_call",
]
