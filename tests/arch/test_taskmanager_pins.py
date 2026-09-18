"""Source-level pins on ``TaskManager`` that the spec-0002/0004 moves must not break.

``voiceai/agent_manager/task_manager.py`` is DO-NOT-REFORMAT until spec 0004 step B13b:
the strangler moves happen *around* it, and dozens of legacy tests monkeypatch its
attributes by name. These meta-tests fail the build the moment a refactor renames one
of the load-bearing methods. (B13b retired the run()-source goodbye-drain substrings:
the order now lives in ``lifecycle.hangup.drain_hangup_goodbye``, pinned behaviorally
by ``tests/test_hangup_goodbye_drain_on_teardown.py``.)
"""

import pytest

from voiceai.agent_manager.task_manager import TaskManager

#: Methods legacy tests and the spec-0004 seams reach by name (mangled name included).
#: B13b retires the run()-source goodbye-drain substrings (the order now lives in
#: lifecycle.hangup.drain_hangup_goodbye, pinned behaviorally); the drain delegator
#: joins the pinned names so the seam itself cannot silently disappear.
PINNED_METHOD_NAMES = (
    "run",
    "sync_history",
    "_handle_transcriber_output",
    "_TaskManager__execute_function_call",
    "_listen_transcriber",
    "drain_hangup_goodbye",
)


@pytest.mark.parametrize("method_name", PINNED_METHOD_NAMES)
def test_pinned_taskmanager_method_exists(method_name: str) -> None:
    """Each pinned method must still exist (and be callable) on ``TaskManager``."""
    attribute = getattr(TaskManager, method_name, None)
    assert callable(attribute), f"TaskManager.{method_name} is pinned by spec 0002 A0 and must not be renamed/removed"
