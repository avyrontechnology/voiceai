"""Source-level pins on ``TaskManager`` that the spec-0002/0004 moves must not break.

``voiceai/agent_manager/task_manager.py`` is DO-NOT-REFORMAT until spec 0004 step B13b:
the strangler moves happen *around* it, and dozens of legacy tests monkeypatch its
attributes by name. These meta-tests fail the build the moment a refactor renames one of
the load-bearing methods or drops the ordered goodbye-drain contract that
``tests/test_hangup_goodbye_drain_on_teardown.py`` pins (the hangup-goodbye gate must
run before the terminal ``sync_history`` trim).
"""

import inspect

import pytest

from voiceai.agent_manager.task_manager import TaskManager

#: Methods legacy tests and the spec-0004 seams reach by name (mangled name included).
PINNED_METHOD_NAMES = (
    "run",
    "sync_history",
    "_handle_transcriber_output",
    "_TaskManager__execute_function_call",
    "_listen_transcriber",
)

#: The two ordered substrings of ``TaskManager.run`` pinned by
#: tests/test_hangup_goodbye_drain_on_teardown.py::test_run_gates_terminal_sync_on_in_flight_hangup.
GOODBYE_GATE_SUBSTRING = "if self.hangup_triggered and not self.conversation_ended:"
GOODBYE_SYNC_SUBSTRING = (
    "await self.sync_history(\n                        self.mark_event_meta_data.mark_event_meta_data.items(),"
)

NOT_FOUND = -1


@pytest.mark.parametrize("method_name", PINNED_METHOD_NAMES)
def test_pinned_taskmanager_method_exists(method_name: str) -> None:
    """Each pinned method must still exist (and be callable) on ``TaskManager``."""
    attribute = getattr(TaskManager, method_name, None)
    assert callable(attribute), f"TaskManager.{method_name} is pinned by spec 0002 A0 and must not be renamed/removed"


def test_run_source_keeps_ordered_goodbye_drain_substrings() -> None:
    """``run()`` still gates the terminal ``sync_history`` behind the hangup drain."""
    source = inspect.getsource(TaskManager.run)
    gate_index = source.find(GOODBYE_GATE_SUBSTRING)
    sync_index = source.find(GOODBYE_SYNC_SUBSTRING)
    assert gate_index != NOT_FOUND, "goodbye-drain gate substring missing from TaskManager.run"
    assert sync_index != NOT_FOUND, "terminal sync_history substring missing from TaskManager.run"
    assert gate_index < sync_index, "goodbye-drain gate must precede the terminal sync_history trim"
