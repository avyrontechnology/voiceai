"""The composition root (spec 0004, B13a): Region-D wiring at its new home.

Three contracts under test, the B-step delegator precedent stood on its head: here
``TaskManager.__init__`` is the thin delegator — it keeps its exact legacy dict
signature (the verbosity suite passes UNMODIFIED) and bundles its arguments into
``CallArgs`` for ``compose_call_session``, which runs the six verbatim phases in
source order. ``from_components`` is the alternate entry over an explicit bundle.
Region D behavior is pinned on concrete values: the seeded kwargs contract, the
InterruptionManager reconfigure, and a live ``from_components`` build.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session import composition
from voiceai.modules.voice.session.composition import CallArgs
from voiceai.modules.voice.session.interruption import InterruptionManager


def _llm_agent_config(**overrides):
    cfg = {
        "model": "gpt-5.4-mini",
        "max_tokens": 150,
        "provider": "openai",
        "temperature": 1,
    }
    cfg.update(overrides)
    return cfg


def _args(**overrides):
    # The offline-constructible shape (mirrors test_llm_verbosity_passthrough).
    task = {
        "task_type": "conversation",
        "toolchain": {"execution": "sequential", "pipelines": [["llm"]]},
        "tools_config": {
            "llm_agent": {
                "agent_type": "simple_llm_agent",
                "agent_flow_type": "streaming",
                "llm_config": _llm_agent_config(),
            },
            "synthesizer": {
                "provider": "elevenlabs",
                "provider_config": {
                    "voice": "Nila",
                    "voice_id": "test",
                    "model": "eleven_turbo_v2_5",
                    "synthesizer_key": "test-key",
                },
                "stream": True,
                "buffer_size": 100,
            },
            "transcriber": {
                "provider": "deepgram",
                "model": "nova-3",
                "language": "en",
                "stream": True,
                "encoding": "linear16",
                "sampling_rate": 16000,
                "endpointing": 250,
            },
            "input": {"provider": "default"},
            "output": {"provider": "default"},
        },
        "task_config": {},
    }
    base = {
        "assistant_name": "agent",
        "task_id": 0,
        "task": task,
        "ws": MagicMock(),
        "input_parameters": None,
        "context_data": None,
        "assistant_id": "agent-1",
        "turn_based_conversation": False,
        "cache": None,
        "input_queue": None,
        "conversation_history": None,
        "output_queue": None,
        "yield_chunks": True,
        "kwargs": {},
    }
    base.update(overrides)
    return CallArgs(**base)


async def test_init_bundles_its_arguments_for_the_composition_root(monkeypatch):
    seen = []
    real = composition.compose_call_session

    def _record(session, args):
        seen.append(args)
        return real(session, args)

    monkeypatch.setattr(composition, "compose_call_session", _record)
    tm = TaskManager.__new__(TaskManager)
    task = _args().task
    TaskManager.__init__(tm, "agent", 0, task, MagicMock(), assistant_id="a-1")
    (bundle,) = seen
    assert isinstance(bundle, CallArgs)
    assert (bundle.assistant_name, bundle.task_id, bundle.assistant_id) == ("agent", 0, "a-1")
    assert bundle.task is task


async def test_from_components_takes_the_explicit_bundle(monkeypatch):
    seen = []
    real = composition.compose_call_session

    def _record(session, args):
        seen.append((session, args))
        return real(session, args)

    monkeypatch.setattr(composition, "compose_call_session", _record)
    args = _args()
    tm = TaskManager.from_components(args)
    assert isinstance(tm, TaskManager)
    (session, bundle) = seen[0]
    assert session is tm
    assert bundle is args


def test_compose_runs_every_phase_in_source_order(monkeypatch):
    order = []

    def _recorder(name):
        def _record(*args, **kwargs):
            order.append(name)
            if name == "adopt_call_config":
                return SimpleNamespace()

        return _record

    for name in (
        "seed_call_state",
        "adopt_call_config",
        "wire_tasks_and_history",
        "wire_session_state",
        "compose_primary_task",
        "compose_runtime_legs",
    ):
        monkeypatch.setattr(composition, name, _recorder(name), raising=True)
    composition.compose_call_session(SimpleNamespace(), _args())
    assert order == [
        "seed_call_state",
        "adopt_call_config",
        "wire_tasks_and_history",
        "wire_session_state",
        "compose_primary_task",
        "compose_runtime_legs",
    ]


async def test_from_components_builds_a_live_session_with_reconfigured_gates():
    tm = TaskManager.from_components(_args(kwargs={"process_interim_results": "false"}))
    assert tm.task_id == 0
    assert tm.assistant_name == "agent"
    assert tm.kwargs["task_manager_instance"] is tm
    assert tm.kwargs["process_interim_results"] == "false"
    assert "transcriber" in tm.tools and "synthesizer" in tm.tools
    # task_id == 0 reconfigures the default-constructed manager (the double-construction
    # quirk): the instance answers the call's gates, not the defaults.
    assert isinstance(tm.interruption_manager, InterruptionManager)
    assert tm.interruption_manager.number_of_words_for_interruption == 3
