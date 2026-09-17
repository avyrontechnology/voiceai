"""The moved handoff-clip bodies (spec 0004, B9a): the switch handoff at its new home.

Three contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_hangup`` precedent.
First, ``TaskManager`` keeps a SAME-NAMED thin delegator per moved name (mangled
``_TaskManager__*`` spellings included) that injects the session. Second, the
process-wide ``HANDOFF_CLIP_CACHE`` moved WITH its owning subsystem and the legacy
module re-binds it BY IDENTITY, so ``tests/test_handoff_prewarm.py``'s
import-and-clear keeps operating on the one real cache. Third, the bodies behave
concretely when driven through their NEW module
(`voiceai.modules.voice.session.language.handoff`) against plain stub sessions, and
this module is the R3 lookup site for the handoff bodies' globals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager import task_manager as task_manager_module
from voiceai.agent_manager.task_manager import TaskManager
from voiceai.helpers.utils import (
    audio_to_mulaw8k,
    audio_to_pcm,
    convert_to_request_log,
    update_prompt_with_context,
)
from voiceai.modules.voice import registry
from voiceai.modules.voice.session.language import handoff


# --- The relocated process-wide cache: identity preserved through the legacy binding ---


def test_handoff_clip_cache_is_the_same_object_through_the_legacy_module():
    assert task_manager_module.HANDOFF_CLIP_CACHE is handoff.HANDOFF_CLIP_CACHE
    assert task_manager_module.HANDOFF_CLIP_CACHE_MAX is handoff.HANDOFF_CLIP_CACHE_MAX
    assert handoff.HANDOFF_CLIP_CACHE_MAX == 256


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_every_handoff_delegator():
    for name in (
        "_TaskManager__play_switch_handoff",
        "_TaskManager__handoff_text_for",
        "_TaskManager__handoff_mulaw_wire",
        "_TaskManager__prewarm_handoff_clips",
        "_TaskManager__handoff_clip_convert",
    ):
        assert callable(getattr(TaskManager, name)), name


async def test_async_handoff_delegators_inject_the_session(monkeypatch):
    play = AsyncMock(return_value=None)
    prewarm = AsyncMock(return_value=None)
    monkeypatch.setattr(handoff, "play_switch_handoff", play)
    monkeypatch.setattr(handoff, "prewarm_handoff_clips", prewarm)
    tm = TaskManager.__new__(TaskManager)
    await getattr(tm, "_TaskManager__play_switch_handoff")("mr")  # noqa: B009
    await getattr(tm, "_TaskManager__prewarm_handoff_clips")()  # noqa: B009
    play.assert_awaited_once_with(tm, "mr")
    prewarm.assert_awaited_once_with(tm)


def test_sync_handoff_delegators_inject_the_session(monkeypatch):
    seen: list = []

    def recorder(tag, result=None):
        def fake(s, *args):
            seen.append((tag, s, *args))
            return result

        return fake

    monkeypatch.setattr(handoff, "handoff_text_for", recorder("text", ""))
    monkeypatch.setattr(handoff, "handoff_mulaw_wire", recorder("wire", False))
    monkeypatch.setattr(handoff, "handoff_clip_convert", recorder("convert"))
    tm = TaskManager.__new__(TaskManager)
    getattr(tm, "_TaskManager__handoff_text_for")("mr")  # noqa: B009
    getattr(tm, "_TaskManager__handoff_mulaw_wire")()  # noqa: B009
    getattr(tm, "_TaskManager__handoff_clip_convert")("synth", b"audio", True)  # noqa: B009
    assert seen == [("text", tm, "mr"), ("wire", tm), ("convert", tm, "synth", b"audio", True)]


# --- R3: this module is the lookup site for the moved bodies' globals ---


def test_lookup_site_binds_the_handoff_globals_by_identity():
    assert handoff.convert_to_request_log is convert_to_request_log
    assert handoff.update_prompt_with_context is update_prompt_with_context
    assert handoff.audio_to_mulaw8k is audio_to_mulaw8k
    assert handoff.audio_to_pcm is audio_to_pcm
    assert handoff.SUPPORTED_OUTPUT_TELEPHONY_HANDLERS is registry.SUPPORTED_OUTPUT_TELEPHONY_HANDLERS


# --- Behavior at the new home ---


def test_handoff_text_renders_agent_name_language_and_context():
    stub = SimpleNamespace(
        switch_handoff_messages={"mr": "Connecting you to {agent_name} in {language}, {customer_name}."},
        _get_voice_name_for_label=lambda label: "Sravya",
        context_data={"recipient_data": {"customer_name": "Asha"}},
    )
    text = handoff.handoff_text_for(stub, "mr")
    assert text == "Connecting you to Sravya in Marathi, Asha."


def test_handoff_text_is_empty_without_a_template():
    stub = SimpleNamespace(switch_handoff_messages={})
    assert handoff.handoff_text_for(stub, "mr") == ""


def test_handoff_mulaw_wire_tracks_the_registry_map():
    stub = SimpleNamespace(tools={"output": SimpleNamespace(get_provider=lambda: "twilio")})
    assert handoff.handoff_mulaw_wire(stub) is True
    stub.tools["output"] = SimpleNamespace(get_provider=lambda: "freeswitch")
    assert handoff.handoff_mulaw_wire(stub) is False


async def test_play_switch_handoff_bails_during_teardown():
    stub = SimpleNamespace(
        hangup_triggered=True,
        conversation_ended=False,
        _TaskManager__handoff_text_for=MagicMock(),
        _synthesize=AsyncMock(),
    )
    await handoff.play_switch_handoff(stub, "mr")
    stub._TaskManager__handoff_text_for.assert_not_called()
    stub._synthesize.assert_not_awaited()


async def test_play_switch_handoff_prewarmed_clip_meta_and_ledger():
    stub = SimpleNamespace(
        hangup_triggered=False,
        conversation_ended=False,
        handoff_audio_cache={"mr": b"\x7f" * 800},
        tools={
            "output": SimpleNamespace(get_provider=lambda: "plivo"),
            "synthesizer": SimpleNamespace(get_engine=lambda: "engine"),
        },
        synthesizer_provider="elevenlabs",
        run_id="run-1",
        conversation_history=MagicMock(),
        _synthesize=AsyncMock(),
        _TaskManager__handoff_text_for=lambda label: "Namaskar!",
        _TaskManager__handoff_mulaw_wire=lambda: True,
        _TaskManager__enqueue_chunk=MagicMock(),
        _TaskManager__record_lid_event=MagicMock(),
    )
    await handoff.play_switch_handoff(stub, "mr")
    chunk, i, n, meta = stub._TaskManager__enqueue_chunk.call_args[0]
    assert chunk == b"\x7f" * 800
    assert (i, n) == (0, 1)
    assert meta["sequence_id"] == -1
    assert meta["format"] == "mulaw"
    assert meta["message_category"] == "handoff"
    assert meta["end_of_llm_stream"] is True
    assert meta["end_of_synthesizer_stream"] is True
    assert meta["is_first_chunk"] is True
    stub._synthesize.assert_not_awaited()  # cached clip goes straight to the transport
    stub.conversation_history.append_assistant.assert_called_once_with(
        "Namaskar!", sequence_id=-1, message_category="handoff"
    )
    stub._TaskManager__record_lid_event.assert_called_once_with(
        {"type": "handoff", "source": "prewarmed", "target": "mr"}
    )


async def test_play_switch_handoff_cold_cache_synthesizes_live():
    stub = SimpleNamespace(
        hangup_triggered=False,
        conversation_ended=False,
        handoff_audio_cache={},
        tools={"output": SimpleNamespace(get_provider=lambda: "plivo")},
        conversation_history=MagicMock(),
        _synthesize=AsyncMock(),
        _TaskManager__handoff_text_for=lambda label: "Namaskar!",
        _TaskManager__enqueue_chunk=MagicMock(),
        _TaskManager__record_lid_event=MagicMock(),
    )
    await handoff.play_switch_handoff(stub, "mr")
    stub._synthesize.assert_awaited_once()
    packet = stub._synthesize.await_args[0][0]
    assert packet["data"] == "Namaskar!"
    assert packet["meta_info"]["cached"] is False
    assert packet["meta_info"]["format"] == "pcm"
    assert packet["meta_info"]["sequence_id"] == -1
    stub._TaskManager__enqueue_chunk.assert_not_called()
    stub._TaskManager__record_lid_event.assert_called_once_with(
        {"type": "handoff", "source": "live", "target": "mr"}
    )
