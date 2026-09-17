"""Lifecycle package: end-of-call concerns of the voice session (spec 0004).

B6 lands `report` — Region V's teardown-report assembly as pure builders over a
`TeardownSnapshot`. B7 lands `hangup` — `CallLifecycle` (flag groups A+D plus the
moved hangup/teardown/watchdog bodies): the package grows exactly along the spec's
target tree.

Only the report and CallLifecycle surfaces are re-exported here; ``run()`` keeps its
verbatim in-place assembly until step B13b swaps it for `snapshot_teardown` + the
builders, and the hangup module's moved bodies stay addressed by their own module
path (it is the monkeypatch lookup site — R3).
"""

from __future__ import annotations

from voiceai.modules.voice.session.lifecycle.hangup import CallLifecycle
from voiceai.modules.voice.session.lifecycle.report import (
    ReportSession,
    TeardownSnapshot,
    build_conversation_report,
    build_followup_report,
    snapshot_teardown,
)

__all__ = [
    "CallLifecycle",
    "ReportSession",
    "TeardownSnapshot",
    "build_conversation_report",
    "build_followup_report",
    "snapshot_teardown",
]
