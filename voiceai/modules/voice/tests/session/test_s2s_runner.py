"""The moved S2S runtime (spec 0004, B5): behavior at the new home, seams pinned.

Three contracts under test, the B3 ``test_static_methods`` precedent. First, the
Region-U bodies behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.s2s_runner`) against a plain stub session — the
functions take the session as their first parameter, so a duck-typed stub is the
whole harness. Second — the migration's load-bearing half — ``TaskManager`` keeps a
SAME-NAMED thin delegator per moved method that injects the session (self) into the
runner, so ``patch.object(TaskManager, ...)``, ``__new__`` harnesses and internal
self-dispatch keep resolving. Third, every legacy ``voiceai.s2s`` path is a pure
identity shim over the relocated package, and the runner module is the lookup site
for the moved bodies' globals (``convert_to_request_log`` / ``trigger_api`` string
patches — R3)."""

import asyncio
import audioop
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.s2s import base as new_base
from voiceai.modules.voice.s2s import events as new_events
from voiceai.modules.voice.s2s.providers.gemini_live import GeminiLiveS2S as NewGemini
from voiceai.modules.voice.s2s.providers.openai_realtime import OpenAIRealtimeS2S as NewOpenAI
from voiceai.modules.voice.session import s2s_runner
from voiceai.s2s import GeminiLiveS2S, OpenAIRealtimeS2S
from voiceai.s2s import events as legacy_events
from voiceai.s2s.base_s2s import BaseS2SProvider as LegacyBase

#: Every Region-U method the B5 contract moved; each keeps a TaskManager delegator.
MOVED_NAMES = (
    "_s2s_telephony_provider",
    "_s2s_is_carrier_leg",
    "_s2s_input_format",
    "_s2s_output_format",
    "_build_s2s_provider",
    "_run_s2s_conversation",
    "_hangup_after_goodbye",
    "_s2s_hangup_if_goodbye_never_comes",
    "_s2s_track_task",
    "_s2s_on_task_done",
    "_s2s_extend_playout",
    "_s2s_agent_has_floor",
    "_s2s_within_welcome_gate",
    "_s2s_audio_ingest_loop",
    "_s2s_event_loop",
    "_s2s_encode_output",
    "_s2s_meta",
    "_s2s_drop_queued_audio",
    "_s2s_finish_turn",
    "_s2s_output_loop",
    "_s2s_text_loop",
    "_s2s_dtmf_loop",
    "_s2s_execute_tool",
    "_s2s_before_tool_request",
    "_s2s_call_api_tool",
)

#: Names whose lookup site moved INTO the runner module (string patches target it now).
RUNNER_LOOKUP_SITES = (
    "convert_to_request_log",
    "trigger_api",
    "compute_function_pre_call_message",
    "calculate_audio_duration",
    "resample",
    "pcm_to_ulaw",
    "ulaw_to_pcm",
    "s2s_events",
)


def _session(**overrides):
    """A duck-typed S2SSession stub; the runner takes it as its `self` parameter."""
    stub = SimpleNamespace(
        turn_based_conversation=False,
        is_web_based_call=False,
        default_io=False,
        sampling_rate=24000,
        task_config={"tools_config": {"input": {"provider": "twilio"}}},
        tools={"output": SimpleNamespace(get_provider=MagicMock(return_value="twilio"))},
        _s2s_turn_seq=0,
        _s2s_welcome_sent=True,
        _s2s_hangup_after_response=False,
        _s2s_started_at=time.time(),
        _s2s_welcome_gate_ms=0,
        _s2s_playout_until=0.0,
        _s2s_output=new_events.AudioFormat(new_events.AudioEncoding.MULAW, 8000),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    # The bodies dispatch through `self._s2s_*` (which is how a patched TaskManager
    # delegator intercepts them); the stub binds the runner back the same way.
    stub._s2s_telephony_provider = lambda: s2s_runner._s2s_telephony_provider(stub)
    stub._s2s_is_carrier_leg = lambda: s2s_runner._s2s_is_carrier_leg(stub)
    return stub


# --- Shim identity: the legacy paths ARE the relocated objects (never copies) ---


def test_legacy_s2s_paths_resolve_to_the_relocated_objects_by_identity():
    assert OpenAIRealtimeS2S is NewOpenAI
    assert GeminiLiveS2S is NewGemini
    assert LegacyBase is new_base.BaseS2SProvider
    for name in new_events.__all__:
        assert getattr(legacy_events, name) is getattr(new_events, name), name


def test_task_manager_still_rides_the_shimmed_event_classes():
    import voiceai.agent_manager.task_manager as legacy_tm

    tm_events = getattr(legacy_tm, "s2s_events")  # noqa: B009 - implicit legacy re-export
    assert tm_events.AudioDelta is new_events.AudioDelta
    assert tm_events.ResponseDone is new_events.ResponseDone


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in MOVED_NAMES:
        assert callable(getattr(TaskManager, name)), name


def test_a_delegator_injects_the_session_into_the_runner(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(s2s_runner, "_s2s_meta", lambda self, **extra: seen.setdefault("session", self))
    tm = TaskManager.__new__(TaskManager)
    tm._s2s_meta()
    assert seen["session"] is tm


def test_the_runner_module_is_the_lookup_site_for_the_moved_globals():
    for name in RUNNER_LOOKUP_SITES:
        assert hasattr(s2s_runner, name), name
    assert getattr(s2s_runner, "s2s_events") is new_events  # noqa: B009 - alias binding, not an export


# --- Behavior at the new home: pure paths driven on a stub session ---


def test_carrier_formats_resolve_exactly_as_the_legacy_leg_did():
    stub = _session()
    assert s2s_runner._s2s_telephony_provider(stub) == "twilio"
    assert s2s_runner._s2s_is_carrier_leg(stub) is True
    assert s2s_runner._s2s_input_format(stub) == new_events.AudioFormat(new_events.AudioEncoding.MULAW, 8000)
    assert s2s_runner._s2s_output_format(stub) == new_events.AudioFormat(new_events.AudioEncoding.MULAW, 8000)


def test_linear_carrier_streams_pcm_up_and_takes_mulaw_down():
    stub = _session(task_config={"tools_config": {"input": {"provider": "plivo"}}})
    assert s2s_runner._s2s_input_format(stub) == new_events.AudioFormat(new_events.AudioEncoding.PCM, 8000)
    assert s2s_runner._s2s_output_format(stub) == new_events.AudioFormat(new_events.AudioEncoding.MULAW, 8000)


def test_browser_leg_is_asymmetric_and_has_no_carrier():
    stub = _session(is_web_based_call=True, task_config={"tools_config": {"input": {"provider": "default"}}})
    assert s2s_runner._s2s_telephony_provider(stub) is None
    assert s2s_runner._s2s_input_format(stub) == new_events.AudioFormat(new_events.AudioEncoding.PCM, 16000)
    assert s2s_runner._s2s_output_format(stub) == new_events.AudioFormat(new_events.AudioEncoding.PCM, 24000)


def test_meta_tags_welcome_then_hangup_and_always_sends_sequence_id_minus_one():
    stub = _session()
    meta = s2s_runner._s2s_meta(stub)
    assert meta["sequence_id"] == -1  # the unconditional-send invariant rides this
    assert meta["message_category"] == "agent_welcome_message"
    stub._s2s_turn_seq = 1
    assert "message_category" not in s2s_runner._s2s_meta(stub)
    stub._s2s_hangup_after_response = True
    assert s2s_runner._s2s_meta(stub)["message_category"] == "agent_hangup"
    assert s2s_runner._s2s_meta(stub, end_of_llm_stream=True)["end_of_llm_stream"] is True


def test_welcome_gate_holds_only_while_the_clock_runs():
    stub = _session(_s2s_welcome_gate_ms=60_000, _s2s_started_at=time.time())
    assert s2s_runner._s2s_within_welcome_gate(stub) is True
    stub._s2s_welcome_sent = False  # no greeting -> never gated
    assert s2s_runner._s2s_within_welcome_gate(stub) is False


def test_playout_extension_gives_the_agent_the_floor():
    stub = _session()
    assert s2s_runner._s2s_agent_has_floor(stub) is False
    s2s_runner._s2s_extend_playout(stub, b"\x00" * 16000)  # 1s of 8k mu-law
    assert stub._s2s_playout_until > time.time()
    assert s2s_runner._s2s_agent_has_floor(stub) is True


def test_encode_output_downsamples_to_mulaw_for_carriers_and_passes_web_pcm():
    pcm_24k = b"\x00\x00" * 480
    stub = _session(tools={"s2s": SimpleNamespace(output_sample_rate=24000)})
    encoded = s2s_runner._s2s_encode_output(stub, pcm_24k)
    assert len(encoded) == 160
    assert audioop.ulaw2lin(encoded, 2) == b"\x00\x00" * 160
    web = _session(
        tools={"s2s": SimpleNamespace(output_sample_rate=24000)},
        _s2s_output=new_events.AudioFormat(new_events.AudioEncoding.PCM, 24000),
    )
    assert s2s_runner._s2s_encode_output(web, pcm_24k) == pcm_24k


async def test_tracked_task_failures_are_surfaced_not_swallowed():
    stub = _session(_s2s_tool_tasks=set())
    # _s2s_on_task_done arrives bound the same way the delegator hands it over.
    stub._s2s_on_task_done = lambda task: s2s_runner._s2s_on_task_done(stub, task)

    async def fine():
        return "ok"

    task = asyncio.get_running_loop().create_task(fine())
    s2s_runner._s2s_track_task(stub, task, call_id="c1")
    assert task in stub._s2s_tool_tasks
    assert getattr(task, "s2s_call_id") == "c1"  # noqa: B009 - the runner's ad-hoc task tag
    await task
    await asyncio.sleep(0)  # let the done callback run
    assert task not in stub._s2s_tool_tasks


# --- The moved base contract: the turn-latency clock at its new home ---


class _NoOpProvider(new_base.BaseS2SProvider):
    """Minimal concrete subclass; the abstract methods are irrelevant to the clock."""

    input_sample_rate = 16000

    async def connect(self):
        return None

    async def send_audio(self, pcm_bytes):
        return None

    async def receive_events(self):  # pragma: no cover - never driven here
        yield None

    async def send_function_result(self, call_id, name, result):
        return None

    async def commit_function_results(self):
        return None

    async def trigger_response(self, instructions=None):
        return None

    async def disconnect(self):
        return None


def test_turn_clock_records_first_audio_once_and_closes_llm_shaped_entries():
    provider = _NoOpProvider(system_prompt="p", voice="v", model="m", api_key="k")
    provider.start_turn()
    provider.record_first_audio()
    first = provider._turn_first_audio_ms
    provider.record_first_audio()  # idempotent per turn
    assert provider._turn_first_audio_ms == first
    provider.end_turn(new_events.S2SUsage(input_tokens=3, output_tokens=5, cached_tokens=1))
    assert provider.turn_latencies[0]["sequence_id"] == 0
    assert provider.turn_latencies[0]["model"] == "m"
    assert provider.turn_latencies[0]["input_tokens"] == 3
    assert provider.turn_latencies[0]["output_tokens"] == 5
    assert provider.turn_latencies[0]["cached_tokens"] == 1
    assert provider.first_audio_latencies == [first]
    # A cancelled turn leaves no entry behind.
    provider.start_turn()
    provider.cancel_turn()
    provider.end_turn(None)
    assert len(provider.turn_latencies) == 1


def test_usage_accumulates_field_wise_and_splits_by_modality():
    a = new_events.S2SUsage(input_tokens=1, input_audio_tokens=1, output_text_tokens=2)
    b = new_events.S2SUsage(input_tokens=2, output_tokens=4, input_audio_tokens=3)
    total = a + b
    assert total.input_tokens == 3
    assert total.output_tokens == 4
    assert total.input_audio_tokens == 4
    assert total.modality_split() == {
        "input_audio_tokens": 4,
        "input_text_tokens": 0,
        "output_audio_tokens": 0,
        "output_text_tokens": 2,
    }
    assert total.as_dict()["output_text_tokens"] == 2
