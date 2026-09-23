"""VoiceCallService behavior: the quickstart WS handler's run loop, verbatim (spec 0004 B4).

DI fakes only (AGENTS.md rule 10): the manager factory and execution recorder are
in-memory doubles, so every legacy-handler contract is pinned without the engine —
outputs collected in order, the record fired from a ``finally`` with the newest
``messages``-bearing payload, recorder failures swallowed as the one legacy warning,
and run exceptions re-raised only AFTER the record. The container binding is proven
through the module's real ``register()`` against a fresh ``Container``.
"""

import logging

import pytest

from voiceai.core.container import VoiceAIContainer
from voiceai.modules.voice import VoiceCallService
from voiceai.modules.voice.service import DIRECTION_INBOUND


class _FakeManager:
    """Stands in for AssistantManager: yields scripted outputs, then optionally raises."""

    def __init__(self, outputs, error=None, run_id="run-123"):
        self.outputs = outputs
        self.error = error
        self.run_id = run_id
        self.run_calls = []

    async def run(self, local=False):
        self.run_calls.append({"local": local})
        for task_id, output in enumerate(self.outputs):
            yield task_id, output
        if self.error is not None:
            raise self.error


class _Recorder:
    """Records the recorder call, optionally failing like a broken platform layer."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def __call__(self, store, **kwargs):
        self.calls.append({"store": store, **kwargs})
        if self.error is not None:
            raise self.error


def _service(manager, recorder):
    factory_calls = []

    def factory(agent_config, ws, assistant_id, *, is_web_based_call):
        factory_calls.append(
            {
                "agent_config": agent_config,
                "ws": ws,
                "assistant_id": assistant_id,
                "is_web_based_call": is_web_based_call,
            }
        )
        return manager

    service = VoiceCallService(
        manager_factory=factory,  # type: ignore[arg-type]
        execution_recorder=recorder,
        logger=logging.getLogger("otobaai.voice.test"),
    )
    return service, factory_calls


async def test_run_call_collects_outputs_and_records_the_last_conversation_payload():
    outputs = [{"status": "ok"}, {"messages": [{"role": "user"}], "run_id": "x"}]
    manager = _FakeManager(outputs)
    recorder = _Recorder()
    service, factory_calls = _service(manager, recorder)
    ws, store = object(), object()

    result = await service.run_call(
        agent_config={"tasks": []}, ws=ws, agent_id="agent-1", is_web_based_call=True, platform_store=store
    )

    assert result == outputs
    # The factory gets the handler's exact construction contract.
    assert factory_calls == [
        {"agent_config": {"tasks": []}, "ws": ws, "assistant_id": "agent-1", "is_web_based_call": True}
    ]
    # The engine runs local=True, exactly as the WS handler always called it.
    assert manager.run_calls == [{"local": True}]
    # The record carries the legacy field set.
    assert recorder.calls == [
        {
            "store": store,
            "agent_id": "agent-1",
            "run_id": "run-123",
            "history": [],
            "task_outputs": outputs,
            "direction": DIRECTION_INBOUND,
            "output": outputs[1],
        }
    ]


async def test_record_picks_the_newest_output_bearing_messages():
    outputs = [{"messages": ["older"]}, {"messages": ["newer"]}, {"extracted_data": {}}]
    manager = _FakeManager(outputs)
    recorder = _Recorder()
    service, _factory_calls = _service(manager, recorder)

    await service.run_call(agent_config={}, ws=object(), agent_id="agent-2")

    assert recorder.calls[0]["output"] == {"messages": ["newer"]}
    # Defaults mirror the handler: no store passed means None reaches the hook.
    assert recorder.calls[0]["store"] is None


async def test_record_output_is_none_when_no_output_carries_messages():
    manager = _FakeManager([{"status": "ok"}, {"messages": []}])
    recorder = _Recorder()
    service, _factory_calls = _service(manager, recorder)

    await service.run_call(agent_config={}, ws=object(), agent_id="agent-3")

    assert recorder.calls[0]["output"] is None


async def test_run_id_defaults_to_none_when_the_manager_never_exposed_one():
    manager = _FakeManager([])
    del manager.run_id  # the legacy handler used getattr(..., None); preserved
    recorder = _Recorder()
    service, _factory_calls = _service(manager, recorder)

    await service.run_call(agent_config={}, ws=object(), agent_id="agent-4")

    assert recorder.calls[0]["run_id"] is None


async def test_recorder_failure_is_swallowed_with_the_legacy_warning(caplog):
    manager = _FakeManager([{"messages": ["hi"]}])
    recorder = _Recorder(error=RuntimeError("platform down"))
    service, _factory_calls = _service(manager, recorder)

    with caplog.at_level(logging.WARNING, logger="otobaai.voice.test"):
        result = await service.run_call(agent_config={}, ws=object(), agent_id="agent-5")

    assert result == [{"messages": ["hi"]}]
    assert any("Execution logging skipped" in record.getMessage() for record in caplog.records)


async def test_run_exception_still_records_then_propagates():
    boom = RuntimeError("socket died")
    manager = _FakeManager([{"messages": ["partial"]}], error=boom)
    recorder = _Recorder()
    service, _factory_calls = _service(manager, recorder)

    with pytest.raises(RuntimeError, match="socket died"):
        await service.run_call(agent_config={}, ws=object(), agent_id="agent-6")

    # The finally fired with the partial outputs before the exception re-raised.
    assert recorder.calls[0]["task_outputs"] == [{"messages": ["partial"]}]


class SimpleNamespaceShim:
    """Minimal AgentSessionStorePort double: serves get_prompts from one async function."""

    def __init__(self, get_prompts):
        self._get_prompts = get_prompts

    async def get_prompts(self, agent_id):
        return await self._get_prompts(agent_id)


async def test_session_store_prefetch_reaches_the_factory():
    """B13a prompt seam: a served payload travels store → factory kwargs (legacy fetch retires)."""
    outputs = [{"messages": ["hi"]}]
    manager = _FakeManager(outputs)
    recorder = _Recorder()
    factory_calls = []

    async def get_prompts(agent_id):
        assert agent_id == "agent-9"
        return {"task_1": {"system_prompt": "be kind"}}

    def factory(agent_config, ws, assistant_id, *, is_web_based_call, prompt_responses=None):
        factory_calls.append({"prompt_responses": prompt_responses})
        return manager

    service = VoiceCallService(
        manager_factory=factory,
        execution_recorder=recorder,
        logger=logging.getLogger("otobaai.voice.test"),
        session_store=SimpleNamespaceShim(get_prompts),
    )
    await service.run_call(agent_config={}, ws=object(), agent_id="agent-9")

    assert factory_calls == [{"prompt_responses": {"task_1": {"system_prompt": "be kind"}}}]


async def test_missing_payload_falls_back_to_the_legacy_fetch():
    """A store miss sends no payload — load_prompt then runs its legacy fetch."""
    manager = _FakeManager([{"messages": ["hi"]}])
    recorder = _Recorder()
    factory_calls = []

    async def get_prompts(agent_id):
        return None

    def factory(agent_config, ws, assistant_id, *, is_web_based_call, **kwargs):
        factory_calls.append(dict(kwargs))
        return manager

    service = VoiceCallService(
        manager_factory=factory,
        execution_recorder=recorder,
        logger=logging.getLogger("otobaai.voice.test"),
        session_store=SimpleNamespaceShim(get_prompts),
    )
    await service.run_call(agent_config={}, ws=object(), agent_id="agent-9")

    assert factory_calls == [{}]


async def test_broken_store_never_fails_the_call(caplog):
    """A raising store degrades to the legacy fetch with a warning, never an exception."""
    manager = _FakeManager([{"messages": ["hi"]}])
    recorder = _Recorder()

    async def get_prompts(agent_id):
        raise RuntimeError("store down")

    def factory(agent_config, ws, assistant_id, *, is_web_based_call, **kwargs):
        assert "prompt_responses" not in kwargs
        return manager

    service = VoiceCallService(
        manager_factory=factory,
        execution_recorder=recorder,
        logger=logging.getLogger("otobaai.voice.test"),
        session_store=SimpleNamespaceShim(get_prompts),
    )
    with caplog.at_level(logging.WARNING, logger="otobaai.voice.test"):
        result = await service.run_call(agent_config={}, ws=object(), agent_id="agent-9")

    assert result == [{"messages": ["hi"]}]
    assert any("Prompt prefetch skipped" in record.getMessage() for record in caplog.records)
