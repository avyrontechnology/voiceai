"""Per-contact dynamic data must reach the live agent's prompts.

Lead lists carry variables per entry (student_name, outstanding, ...),
stored on Execution.variables. On a Talko carrier leg the relay opens a
static-URL socket and forwards Tata's raw start event (ids only), so without
hydration the agent's {placeholders} render empty. These tests pin:

1. hydrate_contact_variables matches the newest non-terminal outbound
   execution for (agent, number) digit-insensitively and setdefault-merges
   its variables into context_data.recipient_data.
2. TalkoInputHandler.call_start captures caller/dialed numbers from the
   start event (alias-tolerant) for that match.
"""

import json

import pytest

from voiceai.input_handlers.telephony_providers.talko import TalkoInputHandler
from voiceai.platform import engine_hook as _engine_hook
from voiceai.platform.engine_hook import hydrate_contact_variables
from voiceai.platform.models import Execution, ExecutionStatus
from voiceai.platform.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean_hydration_cache():
    # The hydration scan cache is process-global: clear between tests so each
    # fresh MemoryStore is actually scanned.
    _engine_hook._CONTACT_EXECUTION_CACHE.clear()
    yield
    _engine_hook._CONTACT_EXECUTION_CACHE.clear()


def _handler():
    import asyncio
    from unittest.mock import MagicMock

    return TalkoInputHandler(
        queues={"transcriber": asyncio.Queue()},
        websocket=MagicMock(),
        input_types={"audio": 0},
        mark_event_meta_data=MagicMock(),
    )


async def _execution(store, **overrides):
    payload = {
        "execution_id": "exec-1",
        "agent_id": "agent-1",
        "direction": "outbound",
        "to_number": "+918585966775",
        "status": ExecutionStatus.QUEUED,
        "variables": {"student_name": "Aarav Sharma", "outstanding": 28500},
    }
    payload.update(overrides)
    execution = Execution(**payload)
    await store.save_execution(execution)
    return execution


async def test_hydration_merges_variables_newest_first():
    store = MemoryStore()
    await _execution(store, execution_id="exec-old", variables={"student_name": "Old"})
    await _execution(store, execution_id="exec-new", variables={"student_name": "Aarav Sharma"})
    context = {"recipient_data": {"to_number": "918585966775"}}
    matched = await hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                              context_data=context)
    assert matched is not None and matched.execution_id == "exec-new"
    assert context["recipient_data"]["student_name"] == "Aarav Sharma"


async def test_hydration_skips_terminal_and_other_agents():
    store = MemoryStore()
    await _execution(store, execution_id="exec-done", status=ExecutionStatus.COMPLETED)
    await _execution(store, execution_id="exec-other", agent_id="agent-2")
    context = {"recipient_data": {}}
    assert await hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                           context_data=context) is None
    assert context["recipient_data"] == {}


async def test_hydration_existing_context_wins():
    store = MemoryStore()
    await _execution(store)
    context = {"recipient_data": {"student_name": "Explicit"}}
    await hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                    context_data=context)
    assert context["recipient_data"]["student_name"] == "Explicit"
    assert context["recipient_data"]["outstanding"] == 28500


async def test_hydration_never_raises():
    assert await hydrate_contact_variables(None, agent_id="a", to_number="+91",
                                          context_data=None) is None
    assert await hydrate_contact_variables(MemoryStore(), agent_id="a", to_number="",
                                           context_data={}) is None


async def test_talko_call_start_captures_numbers():
    handler = _handler()
    await handler.call_start({
        "event": "start",
        "start": {
            "callSid": "CA1",
            "streamSid": "MZ1",
            "customParameters": {"caller": "+919800000001", "called": "917965263087"},
        },
    })
    assert handler.get_stream_sid() == "MZ1"
    assert handler.caller_number == "+919800000001"
    assert handler.dialed_number == "917965263087"


async def test_talko_call_start_flat_number_keys():
    handler = _handler()
    await handler.call_start({"call_id": "CA2", "stream_id": "MZ2",
                              "from_number": "+919800000002", "to_number": "+918585966775"})
    assert handler.caller_number == "+919800000002"
    assert handler.dialed_number == "+918585966775"


async def test_task_manager_hydrates_and_rerenders():
    from unittest.mock import MagicMock

    from voiceai.agent_manager.task_manager import TaskManager

    store = MemoryStore()
    await _execution(store)
    tm = MagicMock()
    tm.kwargs = {
        "platform_store": store,
        "agent_welcome_message": "Namaste {student_name} ji, {outstanding} pending hai.",
        "assistant_id": "agent-1",
    }
    tm.context_data = {"recipient_data": {"to_number": "+918585966775"}}
    tm.prompts = {"system_prompt": "You collect {outstanding} from {student_name}."}
    tm.system_prompt = {"role": "system", "content": "Collect {outstanding} from {student_name}."}
    tm.tools = {"input": MagicMock(caller_number=None, dialed_number=None)}
    await TaskManager._hydrate_contact_context.__get__(tm, TaskManager)()
    assert tm.context_data["recipient_data"]["student_name"] == "Aarav Sharma"
    assert "Aarav Sharma" in tm.kwargs["agent_welcome_message"]
    assert "28500" in tm.kwargs["agent_welcome_message"]
    assert "Aarav Sharma" in tm.prompts["system_prompt"]
    assert "Aarav Sharma" in tm.system_prompt["content"]
    assert json.dumps(tm.context_data)  # still serializable


async def test_task_manager_hydration_waits_for_late_numbers():
    import asyncio
    from unittest.mock import MagicMock

    from voiceai.agent_manager.task_manager import TaskManager

    store = MemoryStore()
    await _execution(store)
    tools_input = MagicMock(caller_number=None, dialed_number=None)
    tm = MagicMock()
    tm.kwargs = {"platform_store": store, "assistant_id": "agent-1"}
    tm.context_data = {"recipient_data": {}}
    tm.prompts = {}
    tm.system_prompt = {}
    tm.tools = {"input": tools_input}

    async def hydrate():
        await TaskManager._hydrate_contact_context.__get__(tm, TaskManager)()

    task = asyncio.ensure_future(hydrate())
    await asyncio.sleep(0.2)
    tools_input.dialed_number = "+918585966775"
    await asyncio.wait_for(task, timeout=5)
    assert tm.context_data["recipient_data"]["student_name"] == "Aarav Sharma"


async def test_engine_record_carries_dial_variables():
    from voiceai.platform.engine_hook import record_engine_execution

    store = MemoryStore()
    await _execution(store, execution_id="exec-dial")
    record = await record_engine_execution(
        store, agent_id="agent-1", run_id="run-9",
        history=[{"role": "assistant", "content": "Namaste"}],
        task_outputs=[], to_number="+918585966775", direction="inbound",
    )
    assert record is not None
    assert record.variables.get("student_name") == "Aarav Sharma"
    assert record.variables.get("outstanding") == 28500


async def test_engine_record_without_match_keeps_empty_variables():
    from voiceai.platform.engine_hook import record_engine_execution

    store = MemoryStore()
    record = await record_engine_execution(
        store, agent_id="agent-1", run_id="run-10",
        history=[], task_outputs=[], to_number="+910000000000", direction="inbound",
    )
    assert record is not None and record.variables == {}


async def test_engine_record_web_leg_gets_no_variables():
    from voiceai.platform.engine_hook import record_engine_execution

    store = MemoryStore()
    await _execution(store, execution_id="exec-dial")
    record = await record_engine_execution(
        store, agent_id="agent-1", run_id="run-11",
        history=[], task_outputs=[], to_number="+918585966775",
        direction="inbound", is_web_based_call=True,
    )
    assert record is not None and record.variables == {}


async def test_task_manager_hydration_builds_context_when_none():
    import asyncio
    from unittest.mock import MagicMock

    from voiceai.agent_manager.task_manager import TaskManager

    store = MemoryStore()
    await _execution(store)
    tools_input = MagicMock(caller_number=None, dialed_number="+918585966775")
    tm = MagicMock()
    tm.kwargs = {
        "platform_store": store,
        "assistant_id": "agent-1",
        "agent_welcome_message": "Namaste {student_name} ji.",
    }
    tm.context_data = None
    tm.prompts = {}
    tm.system_prompt = {}
    tm.tools = {"input": tools_input}
    await TaskManager._hydrate_contact_context.__get__(tm, TaskManager)()
    assert isinstance(tm.context_data, dict)
    assert tm.context_data["recipient_data"]["student_name"] == "Aarav Sharma"
    assert "Aarav Sharma" in tm.kwargs["agent_welcome_message"]
