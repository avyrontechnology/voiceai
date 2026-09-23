"""The moved welcome flow (spec 0004, B8): first-message senders at their new home.

Three contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_hangup`` precedent.
First, the welcome bodies behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.welcome`) against plain stub sessions — the functions
take the session as their first parameter, so a duck-typed stub is the whole harness.
Second, ``TaskManager`` keeps a SAME-NAMED thin delegator per moved method (mangled
``_TaskManager__*`` spellings included) that injects the session (self), so
instance-attr ``AsyncMock`` overrides, ``__new__`` harnesses, the
init-event-observable registration and internal self-dispatch keep resolving. Third,
the welcome module is the LOOKUP SITE for the moved bodies' globals
(``create_ws_data_packet`` / ``convert_to_request_log`` / the audio helpers /
``update_prompt_with_context`` — R3), pinned by identity and exercised through
monkeypatches on the new path."""

import asyncio
import audioop
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.helpers import utils as legacy_utils
from voiceai.modules.voice.session import welcome

#: Every welcome method the B8 contract moved; each keeps a TaskManager delegator.
MOVED_NAMES = (
    "_TaskManager__forced_first_message",
    "_TaskManager__synthesize_welcome_audio",
    "_TaskManager__first_message",
    "handle_init_event",
)

#: Names whose lookup site moved INTO the welcome module (string patches target it now).
WELCOME_LOOKUP_SITES = (
    "calculate_audio_duration",
    "convert_to_request_log",
    "create_ws_data_packet",
    "get_synth_audio_format",
    "pcm_to_ulaw",
    "resample",
    "update_prompt_with_context",
    "wav_bytes_to_pcm",
)


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in MOVED_NAMES:
        assert callable(getattr(TaskManager, name)), name


async def test_forced_first_message_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(welcome, "forced_first_message", moved)
    tm = TaskManager.__new__(TaskManager)
    # getattr: outside the class body mypy does not model the compile-time mangling.
    await getattr(tm, "_TaskManager__forced_first_message")(timeout=3.0)  # noqa: B009
    moved.assert_awaited_once_with(tm, timeout=3.0)


async def test_synthesize_welcome_audio_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=b"pcm")
    monkeypatch.setattr(welcome, "synthesize_welcome_audio", moved)
    tm = TaskManager.__new__(TaskManager)
    assert await getattr(tm, "_TaskManager__synthesize_welcome_audio")("hello") == b"pcm"  # noqa: B009
    moved.assert_awaited_once_with(tm, "hello")


async def test_first_message_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(welcome, "first_message", moved)
    tm = TaskManager.__new__(TaskManager)
    await getattr(tm, "_TaskManager__first_message")(timeout=2.0)  # noqa: B009
    moved.assert_awaited_once_with(tm, timeout=2.0)


async def test_handle_init_event_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(welcome, "handle_init_event", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm.handle_init_event({"context_data": {"a": 1}})
    moved.assert_awaited_once_with(tm, {"context_data": {"a": 1}})


# --- R3: the welcome module is the lookup site for the moved bodies' globals ---


def test_welcome_module_is_the_lookup_site_for_its_globals():
    for name in WELCOME_LOOKUP_SITES:
        assert getattr(welcome, name) is getattr(legacy_utils, name), name


# --- forced_first_message: the telephony welcome path ---


def _forced_session(**overrides):
    stub = SimpleNamespace(
        welcome_message_delay=None,
        kwargs={"agent_welcome_message": "Namaste"},
        task_config={"tools_config": {"output": {"format": "wav"}}},
        tools={
            "input": SimpleNamespace(set_welcome_message_played=MagicMock(), update_is_audio_being_played=MagicMock()),
            "output": SimpleNamespace(get_provider=MagicMock(return_value="twilio"), handle=AsyncMock()),
            "synthesizer": SimpleNamespace(get_engine=MagicMock(return_value="engine-1")),
        },
        preloaded_welcome_audio=b"\x01\x02" * 800,
        stream_sid="SS9",
        conversation_history=MagicMock(),
        synthesizer_provider="kalpa",
        run_id="run-1",
        sampling_rate=8000,
        should_record=False,
        welcome_message_duration_ms=None,
        conversation_recording={"output": []},
        _TaskManager__await_stream_sid=AsyncMock(return_value=True),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_forced_first_message_sends_the_preloaded_welcome(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(welcome, "convert_to_request_log", log)
    stub = _forced_session()
    await welcome.forced_first_message(stub)

    stub.tools["output"].handle.assert_awaited_once()
    message = stub.tools["output"].handle.await_args[0][0]
    assert message["data"] == b"\x01\x02" * 800
    meta = message["meta_info"]
    assert meta["message_category"] == "agent_welcome_message"
    assert meta["sequence_id"] == -1  # ungated welcome send (behavior-invariant checklist)
    assert meta["cached"] is True
    assert meta["io"] == "twilio"
    assert meta["type"] == "audio"
    assert meta["format"] == "pcm"
    assert meta["is_first_chunk"] is True
    assert meta["end_of_synthesizer_stream"] is True
    assert meta["chunk_id"] == 1
    assert meta["is_first_chunk_of_entire_response"] is True
    assert meta["is_final_chunk_of_entire_response"] is True
    stub.tools["input"].update_is_audio_being_played.assert_called_once_with(True)
    stub.conversation_history.append_welcome_message.assert_called_once_with("Namaste")
    # 1600 bytes of 16-bit PCM at 8kHz is exactly 100ms.
    assert stub.welcome_message_duration_ms == 100.0
    assert log.call_args.kwargs["model"] == "kalpa"
    assert log.call_args.kwargs["engine"] == "engine-1"
    assert log.call_args.kwargs["run_id"] == "run-1"


async def test_forced_first_message_records_the_welcome_when_recording():
    stub = _forced_session(should_record=True)
    await welcome.forced_first_message(stub)
    assert len(stub.conversation_recording["output"]) == 1
    entry = stub.conversation_recording["output"][0]
    assert entry["data"] == b"\x01\x02" * 800
    assert entry["duration"] == 0.1


async def test_forced_first_message_converts_to_ulaw_for_sip_trunk():
    pcm = b"\x00\x01" * 100
    stub = _forced_session(preloaded_welcome_audio=pcm)
    stub.tools["output"].get_provider.return_value = "sip-trunk"
    await welcome.forced_first_message(stub)
    message = stub.tools["output"].handle.await_args[0][0]
    assert message["data"] == audioop.lin2ulaw(pcm, 2)
    assert message["meta_info"]["format"] == "ulaw"


async def test_forced_first_message_without_audio_marks_the_welcome_played():
    # No preloaded audio and no text: nothing to send, so the mark that will never
    # arrive is short-circuited immediately (the silent-call guard).
    stub = _forced_session(preloaded_welcome_audio=None, kwargs={"agent_welcome_message": None})
    await welcome.forced_first_message(stub)
    stub.tools["input"].set_welcome_message_played.assert_called_once_with(True)
    stub.tools["output"].handle.assert_not_awaited()


async def test_forced_first_message_empty_text_drops_even_preloaded_audio():
    # Preserved quirk: an explicitly empty welcome text nulls the audio AFTER the
    # preload check, so even preloaded audio is dropped and the welcome marks played.
    stub = _forced_session(kwargs={"agent_welcome_message": ""})
    await welcome.forced_first_message(stub)
    stub.tools["input"].set_welcome_message_played.assert_called_once_with(True)
    stub.tools["output"].handle.assert_not_awaited()


async def test_forced_first_message_synthesizes_when_no_preload(monkeypatch):
    synth = AsyncMock(return_value=b"fresh-pcm")
    monkeypatch.setattr(welcome, "synthesize_welcome_audio", synth)
    stub = _forced_session(preloaded_welcome_audio=None)
    stub._TaskManager__synthesize_welcome_audio = AsyncMock(return_value=b"fresh-pcm")
    await welcome.forced_first_message(stub)
    # The body dispatches through the session's mangled spelling, so a patched
    # TaskManager delegator (or harness attr) intercepts — not the module function.
    stub._TaskManager__synthesize_welcome_audio.assert_awaited_once_with("Namaste")
    synth.assert_not_awaited()
    assert stub.tools["output"].handle.await_args[0][0]["data"] == b"fresh-pcm"


async def test_forced_first_message_bails_when_the_stream_sid_never_lands():
    stub = _forced_session()
    stub._TaskManager__await_stream_sid = AsyncMock(return_value=False)
    await welcome.forced_first_message(stub, timeout=4.0)
    stub._TaskManager__await_stream_sid.assert_awaited_once_with(timeout=4.0)
    stub.tools["output"].handle.assert_not_awaited()
    stub.tools["input"].set_welcome_message_played.assert_not_called()


async def test_forced_first_message_sleeps_the_configured_delay(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(welcome, "asyncio", SimpleNamespace(sleep=sleep))
    stub = _forced_session(welcome_message_delay=250)
    await welcome.forced_first_message(stub)
    sleep.assert_awaited_once_with(0.25)


async def test_forced_first_message_duration_failure_falls_back_to_256ms(monkeypatch):
    monkeypatch.setattr(welcome, "calculate_audio_duration", MagicMock(side_effect=RuntimeError("bad math")))
    stub = _forced_session()
    await welcome.forced_first_message(stub)
    # Preserved quirk: the 0.256s fallback stamps the duration and the send still counts.
    assert stub.welcome_message_duration_ms == 256.0
    stub.tools["output"].handle.assert_awaited_once()


# --- synthesize_welcome_audio: the browser-leg TTS fallback ---


async def test_synthesize_welcome_audio_needs_a_synthesizer_and_text():
    assert await welcome.synthesize_welcome_audio(SimpleNamespace(tools={}), "hi") is None
    no_synthesize = SimpleNamespace(tools={"synthesizer": SimpleNamespace()})
    assert await welcome.synthesize_welcome_audio(no_synthesize, "hi") is None
    synth = SimpleNamespace(synthesize=AsyncMock(return_value=b"pcm"), sampling_rate=8000)
    stub = SimpleNamespace(tools={"synthesizer": synth}, sampling_rate=8000)
    assert await welcome.synthesize_welcome_audio(stub, "   ") is None
    synth.synthesize.assert_not_awaited()


async def test_synthesize_welcome_audio_never_raises_on_tts_failure():
    synth = SimpleNamespace(synthesize=AsyncMock(side_effect=RuntimeError("tts down")), sampling_rate=8000)
    stub = SimpleNamespace(tools={"synthesizer": synth}, sampling_rate=8000)
    assert await welcome.synthesize_welcome_audio(stub, "hi") is None


async def test_synthesize_welcome_audio_prefers_the_synth_processor():
    synth = SimpleNamespace(
        synthesize=AsyncMock(return_value=b"raw-payload"),
        _process_audio_data=MagicMock(return_value=b"processed-pcm"),
        sampling_rate=8000,
    )
    stub = SimpleNamespace(tools={"synthesizer": synth}, sampling_rate=8000)
    assert await welcome.synthesize_welcome_audio(stub, "hi") == b"processed-pcm"
    synth._process_audio_data.assert_called_once_with(b"raw-payload")


async def test_synthesize_welcome_audio_unwraps_a_wav_container(monkeypatch):
    monkeypatch.setattr(welcome, "get_synth_audio_format", MagicMock(return_value="wav"))
    unwrap = MagicMock(return_value=b"bare-pcm")
    monkeypatch.setattr(welcome, "wav_bytes_to_pcm", unwrap)
    synth = SimpleNamespace(synthesize=AsyncMock(return_value=b"RIFF-wav-bytes"), sampling_rate=8000)
    stub = SimpleNamespace(tools={"synthesizer": synth}, sampling_rate=8000)
    assert await welcome.synthesize_welcome_audio(stub, "hi") == b"bare-pcm"
    unwrap.assert_called_once_with(b"RIFF-wav-bytes")


async def test_synthesize_welcome_audio_decodes_base64_text_payloads():
    import base64 as b64

    payload = b64.b64encode(b"text-pcm").decode()
    synth = SimpleNamespace(synthesize=AsyncMock(return_value=payload), sampling_rate=8000)
    stub = SimpleNamespace(tools={"synthesizer": synth}, sampling_rate=8000)
    # The decoded str becomes bytes; a non-wav payload passes through as bare PCM.
    assert await welcome.synthesize_welcome_audio(stub, "hi") == b"text-pcm"


async def test_synthesize_welcome_audio_resamples_to_the_call_rate(monkeypatch):
    resampled = MagicMock(return_value=b"resampled-pcm")
    monkeypatch.setattr(welcome, "resample", resampled)
    synth = SimpleNamespace(synthesize=AsyncMock(return_value=b"hi-rate-pcm"), sampling_rate=24000)
    stub = SimpleNamespace(tools={"synthesizer": synth}, sampling_rate=8000)
    assert await welcome.synthesize_welcome_audio(stub, "hi") == b"resampled-pcm"
    resampled.assert_called_once_with(b"hi-rate-pcm", 8000, format="pcm", original_sample_rate=24000)


# --- first_message: web synthesis now, telephony gated on stream_sid ---


def _first_session(**overrides):
    stub = SimpleNamespace(
        is_web_based_call=False,
        turn_based_conversation=False,
        default_io=False,
        stream_sid=None,
        stream_sid_ts=None,
        kwargs={"agent_welcome_message": "Hello there"},
        task_config={"tools_config": {"output": {"format": "pcm"}}},
        tools={
            "input": SimpleNamespace(get_stream_sid=MagicMock(return_value="SS1")),
            "output": SimpleNamespace(get_provider=MagicMock(return_value="plivo"), handle=AsyncMock()),
        },
        conversation_history=MagicMock(),
        _synthesize=AsyncMock(),
        _TaskManager__process_end_of_conversation=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_first_message_web_call_synthesizes_immediately():
    stub = _first_session(is_web_based_call=True, stream_sid="web-sid")
    await welcome.first_message(stub)
    stub._synthesize.assert_awaited_once()
    packet = stub._synthesize.await_args[0][0]
    assert packet["data"] == "Hello there"
    meta = packet["meta_info"]
    assert meta["io"] == "default"
    assert meta["cached"] is False
    assert meta["sequence_id"] == -1
    assert meta["stream_sid"] == "web-sid"
    assert meta["message_category"] == "agent_welcome_message"
    stub.conversation_history.append_welcome_message.assert_called_once_with("Hello there")
    assert stub.stream_sid_ts is not None
    stub.tools["input"].get_stream_sid.assert_not_called()  # no carrier poll on web legs


async def test_first_message_telephony_waits_for_the_stream_sid_then_synthesizes():
    stub = _first_session()
    await welcome.first_message(stub, timeout=1.0)
    assert stub.stream_sid == "SS1"
    assert stub.stream_sid_ts is not None
    stub._synthesize.assert_awaited_once()
    meta = stub._synthesize.await_args[0][0]["meta_info"]
    assert meta["cached"] is True
    assert meta["io"] == "plivo"
    assert meta["stream_sid"] == "SS1"
    stub.conversation_history.append_welcome_message.assert_called_once_with("Hello there")
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()


async def test_first_message_turn_based_wraps_the_text_in_stream_markers():
    stub = _first_session(turn_based_conversation=True)
    await welcome.first_message(stub, timeout=1.0)
    stub._synthesize.assert_not_awaited()
    sent = [call.args[0] for call in stub.tools["output"].handle.await_args_list]
    assert [packet["data"] for packet in sent] == ["<beginning_of_stream>", "Hello there", "<end_of_stream>"]
    assert all(packet["meta_info"]["type"] == "text" for packet in sent)


async def test_first_message_timeout_ends_the_conversation():
    stub = _first_session()
    stub.tools["input"].get_stream_sid.return_value = None
    await welcome.first_message(stub, timeout=0.05)
    stub._TaskManager__process_end_of_conversation.assert_awaited_once_with()
    stub._synthesize.assert_not_awaited()


async def test_first_message_default_io_sends_nothing():
    stub = _first_session(default_io=True)
    await welcome.first_message(stub, timeout=1.0)
    stub._synthesize.assert_not_awaited()
    stub.tools["output"].handle.assert_not_awaited()
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()


async def test_first_message_blank_text_skips_the_history_append():
    stub = _first_session(kwargs={"agent_welcome_message": "   "})
    await welcome.first_message(stub, timeout=1.0)
    stub.conversation_history.append_welcome_message.assert_not_called()
    stub._synthesize.assert_awaited_once()  # the packet still goes out, text and all


# --- handle_init_event: web-call context injection, then ack + welcome ---


def _init_session(**overrides):
    history = MagicMock()
    history.__len__.return_value = 2
    stub = SimpleNamespace(
        context_data=None,
        prompts={"system_prompt": "Base {name}"},
        system_prompt={"role": "system", "content": "Sys {name}"},
        conversation_history=history,
        call_hangup_message_config=None,
        kwargs={"agent_welcome_message": "Hi {name}"},
        first_message_task=None,
        tools={"output": SimpleNamespace(send_init_acknowledgement=AsyncMock())},
        _TaskManager__first_message=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_handle_init_event_injects_client_context_everywhere():
    stub = _init_session(call_hangup_message_config={"en": "Bye {name}", "hi": "Alvida {name}"})
    await welcome.handle_init_event(stub, {"context_data": {"name": "Sam"}})
    assert stub.context_data == {"recipient_data": {"name": "Sam"}}
    assert stub.prompts["system_prompt"] == "Base Sam"
    assert stub.system_prompt["content"] == "Sys Sam"
    stub.conversation_history.update_system_prompt.assert_called_once_with("Sys Sam")
    assert stub.call_hangup_message_config == {"en": "Bye Sam", "hi": "Alvida Sam"}
    assert stub.kwargs["agent_welcome_message"] == "Hi Sam"
    stub.conversation_history.update_welcome_message.assert_called_once_with("Hi Sam")


async def test_handle_init_event_acks_and_schedules_the_welcome():
    stub = _init_session()
    await welcome.handle_init_event(stub, {"context_data": {"name": "Sam"}})
    stub.tools["output"].send_init_acknowledgement.assert_awaited_once()
    assert stub.first_message_task is not None
    await asyncio.wait_for(stub.first_message_task, timeout=5)
    # Scheduled through the mangled spelling: a patched TaskManager delegator intercepts.
    stub._TaskManager__first_message.assert_awaited_once()


async def test_handle_init_event_context_failure_never_blocks_the_welcome():
    # Preserved contract: a broken context update (playground init without context)
    # must still ack and play the welcome, or the call stays silent forever.
    stub = _init_session(prompts=None)  # self.prompts["system_prompt"] raises TypeError
    await welcome.handle_init_event(stub, {"context_data": {"name": "Sam"}})
    stub.tools["output"].send_init_acknowledgement.assert_awaited_once()
    assert stub.first_message_task is not None
    await asyncio.wait_for(stub.first_message_task, timeout=5)
    stub._TaskManager__first_message.assert_awaited_once()


async def test_handle_init_event_only_rewrites_a_two_message_history():
    stub = _init_session()
    stub.conversation_history.__len__.return_value = 4
    await welcome.handle_init_event(stub, {"context_data": {"name": "Sam"}})
    stub.conversation_history.update_welcome_message.assert_not_called()
