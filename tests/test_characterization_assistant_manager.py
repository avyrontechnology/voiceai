"""Characterization net for AssistantManager's task fan-out (spec 0004 B1).

AssistantManager had ZERO tests. Before spec 0004 replaces its seam with
VoiceCallService.run_call (step B4), this file pins the observable contract:
the welcome-message preparation in __init__ and the per-task TaskManager
construction/loading/run/yield loop in run(). TaskManager itself is faked at
its lookup site (voiceai.agent_manager.assistant_manager.TaskManager) so only
the fan-out is under test.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from voiceai.agent_manager.assistant_manager import AssistantManager
from voiceai.models import AGENT_WELCOME_MESSAGE


class _FakeTaskManager:
    """Records the exact construction/load/run contract AssistantManager uses."""

    def __init__(self, assistant_name, task_id, task, ws, **kwargs):
        self.assistant_name = assistant_name
        self.task_id = task_id
        self.task = task
        self.ws = ws
        self.kwargs = kwargs
        self.load_prompt_calls = []
        # Canned per-instance output, replaced by tests via outputs_by_task_id.
        self.output = {"answer": f"task-{task_id}"}
        _FakeTaskManager.instances.append(self)

    instances = []

    async def load_prompt(self, assistant_name, task_id, local=False, **kwargs):
        self.load_prompt_calls.append({"assistant_name": assistant_name, "task_id": task_id, "local": local})

    async def run(self):
        self.output = dict(_FakeTaskManager.outputs_by_task_id.get(self.task_id, self.output))
        return self.output

    outputs_by_task_id = {}


@pytest.fixture(autouse=True)
def _fresh_fake_tm():
    _FakeTaskManager.instances = []
    _FakeTaskManager.outputs_by_task_id = {}
    with patch("voiceai.agent_manager.assistant_manager.TaskManager", _FakeTaskManager):
        yield


def _agent_config(tasks=None, **overrides):
    config = {
        "agent_name": "billing-agent",
        "assistant_name": "legacy-name",
        "agent_welcome_message": "Hello {name}, how can I help?",
        "tasks": tasks if tasks is not None else [{"task_type": "conversation"}],
    }
    config.update(overrides)
    return config


def _context_data(name="Asha"):
    return {"recipient_data": {"name": name}}


async def _collect(manager, **run_kwargs):
    return [item async for item in manager.run(**run_kwargs)]


# ---------------------------------------------------------------------------
# __init__: welcome-message preparation
# ---------------------------------------------------------------------------


def test_welcome_message_is_context_substituted_for_non_web_calls():
    manager = AssistantManager(_agent_config(), ws=MagicMock(), context_data=_context_data())
    assert manager.kwargs["agent_welcome_message"] == "Hello Asha, how can I help?"


def test_web_based_call_keeps_the_raw_welcome_template():
    manager = AssistantManager(
        _agent_config(), ws=MagicMock(), context_data=_context_data(), is_web_based_call=True
    )
    assert manager.kwargs["agent_welcome_message"] == "Hello {name}, how can I help?"


def test_missing_welcome_message_defaults_to_the_recorded_notice():
    config = _agent_config()
    del config["agent_welcome_message"]
    manager = AssistantManager(config, ws=MagicMock(), context_data=None, is_web_based_call=True)
    assert manager.kwargs["agent_welcome_message"] == AGENT_WELCOME_MESSAGE


def test_init_seeds_run_id_and_task_states():
    config = _agent_config(tasks=[{"task_type": "conversation"}, {"task_type": "summarization"}])
    manager = AssistantManager(config, ws=MagicMock(), context_data=_context_data())
    uuid.UUID(manager.run_id)  # a fresh uuid4 until run() overrides it
    assert manager.task_states == [False, False]


# ---------------------------------------------------------------------------
# run(): the fan-out
# ---------------------------------------------------------------------------


async def test_run_constructs_one_task_manager_per_task_with_the_legacy_signature():
    ws = MagicMock()
    cache, in_q, out_q, history = object(), object(), object(), [{"role": "user", "content": "hi"}]
    tasks = [{"task_type": "conversation"}, {"task_type": "summarization"}]
    manager = AssistantManager(
        _agent_config(tasks=tasks),
        ws=ws,
        assistant_id="agent-1",
        context_data=_context_data(),
        conversation_history=history,
        turn_based_conversation=False,
        cache=cache,
        input_queue=in_q,
        output_queue=out_q,
        extra_kwarg="carried",
    )

    await _collect(manager)

    assert len(_FakeTaskManager.instances) == 2
    for task_id, tm in enumerate(_FakeTaskManager.instances):
        assert tm.assistant_name == "billing-agent"  # agent_name preferred over assistant_name
        assert tm.task_id == task_id
        assert tm.task is tasks[task_id]
        assert tm.ws is ws
        assert tm.kwargs["assistant_id"] == "agent-1"
        assert tm.kwargs["run_id"] == manager.run_id
        assert tm.kwargs["cache"] is cache
        assert tm.kwargs["input_queue"] is in_q
        assert tm.kwargs["output_queue"] is out_q
        assert tm.kwargs["conversation_history"] is history
        assert tm.kwargs["extra_kwarg"] == "carried"  # **kwargs fan through unmodified
        assert "agent_welcome_message" in tm.kwargs
    assert manager.task_states == [True, True]


async def test_run_falls_back_to_assistant_name_when_agent_name_is_absent():
    config = _agent_config()
    del config["agent_name"]
    manager = AssistantManager(config, ws=MagicMock(), context_data=_context_data())

    await _collect(manager)

    tm = _FakeTaskManager.instances[0]
    assert tm.assistant_name == "legacy-name"
    assert tm.load_prompt_calls == [{"assistant_name": "legacy-name", "task_id": 0, "local": False}]


async def test_run_forwards_local_flag_to_load_prompt_and_overrides_run_id():
    manager = AssistantManager(_agent_config(), ws=MagicMock(), context_data=_context_data())

    results = await _collect(manager, local=True, run_id="fixed-run-id")

    assert manager.run_id == "fixed-run-id"
    tm = _FakeTaskManager.instances[0]
    assert tm.load_prompt_calls[0]["local"] is True
    assert tm.kwargs["run_id"] == "fixed-run-id"  # __init__ uuid replaced before fan-out
    assert results[0][1]["run_id"] == "fixed-run-id"


async def test_run_yields_deepcopies_stamped_with_run_id():
    manager = AssistantManager(_agent_config(), ws=MagicMock(), context_data=_context_data())

    results = await _collect(manager)

    (task_id, yielded), tm = results[0], _FakeTaskManager.instances[0]
    assert task_id == 0
    assert yielded["run_id"] == manager.run_id
    assert yielded is not tm.output  # a deepcopy protects internal state
    yielded["answer"] = "mutated"
    assert tm.output["answer"] == "task-0"


async def test_task_zero_output_becomes_input_parameters_for_later_tasks():
    tasks = [{"task_type": "conversation"}, {"task_type": "summarization"}]
    manager = AssistantManager(_agent_config(tasks=tasks), ws=MagicMock(), context_data=_context_data())

    await _collect(manager)

    first, second = _FakeTaskManager.instances
    assert first.kwargs["input_parameters"] is None
    # The SAME dict object (not the yielded deepcopy) is handed to the next task.
    assert second.kwargs["input_parameters"] is first.output
    assert second.kwargs["input_parameters"]["run_id"] == manager.run_id


async def test_extraction_task_injects_extraction_details_into_input_parameters():
    tasks = [
        {"task_type": "conversation"},
        {"task_type": "extraction"},
        {"task_type": "summarization"},
    ]
    _FakeTaskManager.outputs_by_task_id = {1: {"extracted_data": {"amount": 42}}}
    manager = AssistantManager(_agent_config(tasks=tasks), ws=MagicMock(), context_data=_context_data())

    await _collect(manager)

    first, _second, third = _FakeTaskManager.instances
    assert first.output["extraction_details"] == {"amount": 42}
    assert third.kwargs["input_parameters"]["extraction_details"] == {"amount": 42}
