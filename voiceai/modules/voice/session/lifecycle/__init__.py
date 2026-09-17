"""Lifecycle package: end-of-call concerns of the voice session (spec 0004).

B6 lands `report` — Region V's teardown-report assembly as pure builders over a
`TeardownSnapshot`. B7 adds `hangup` (CallLifecycle + the completion checks): the
package grows exactly along the spec's target tree.

Only the report surface is re-exported here; ``run()`` keeps its verbatim in-place
assembly until step B13b swaps it for `snapshot_teardown` + the builders.
"""

from __future__ import annotations

from voiceai.modules.voice.session.lifecycle.report import (
    ReportSession,
    TeardownSnapshot,
    build_conversation_report,
    build_followup_report,
    snapshot_teardown,
)

__all__ = [
    "ReportSession",
    "TeardownSnapshot",
    "build_conversation_report",
    "build_followup_report",
    "snapshot_teardown",
]
