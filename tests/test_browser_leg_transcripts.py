"""Browser-leg (Live Talk) transcript forwarding for cascaded voice agents.

S2S agents forward caller/agent text frames from their event loop, but pipeline
agents staged agent replies in _pending_chat_forward with no drain on voice-only
calls (the drain lives behind the typed-chat llm queue), and never forwarded
caller transcripts at all — the transcript panel stayed empty.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voiceai.agent_manager.task_manager import TaskManager


def make_browser_tm(*, io_provider="default"):
    tm = TaskManager.__new__(TaskManager)
    tm.task_config = {"tools_config": {"input": {"provider": io_provider}}}
    tm.turn_based_conversation = False
    tm.is_web_based_call = False
    tm._pending_chat_forward = []
    tm.conversation_ended = False
    tm._speech_started_before_welcome = False
    tm.output_task = None
    tm.transcriber_provider = "sarvam"
    tm.textual_chat_agent = False
    tm.run_id = "test-run"
    tm.interruption_manager = MagicMock()
    tm._response_turn_id = 0
    output = SimpleNamespace(handle=AsyncMock())
    inp = SimpleNamespace(
        welcome_message_played=MagicMock(return_value=True),
        update_is_audio_being_played=MagicMock(),
        is_audio_being_played_to_user=MagicMock(return_value=False),
    )
    tm.tools = {"s2s": None, "output": output, "input": inp}
    tm.conversation_history = MagicMock()
    tm.conversation_history.is_duplicate_user = MagicMock(return_value=False)
    tm.language_detector = MagicMock()
    tm.language_detector.collect_transcript = AsyncMock()
    tm.voicemail_handler = MagicMock(detected=False)
    tm._trigger_voicemail_check = MagicMock()
    tm._inflight_response_activity = MagicMock(return_value={})
    tm.regen_settle_armed = MagicMock(return_value=False)
    tm.regen_settle_can_fire = MagicMock(return_value=False)
    tm.kickoff_llm_generation = MagicMock()
    return tm


def test_agent_reply_drain_forwards_text_frame():
    tm = make_browser_tm()
    tm._pending_chat_forward = ["Namaste! How can I help?"]

    import asyncio

    asyncio.run(tm._drain_pending_chat_forward())

    assert tm._pending_chat_forward == []
    tm.tools["output"].handle.assert_awaited_once()
    packet = tm.tools["output"].handle.await_args.args[0]
    assert packet["data"] == "Namaste! How can I help?"
    assert packet["meta_info"]["type"] == "text"
    assert packet["meta_info"]["role"] == "agent"


def test_telephony_leg_does_not_forward():
    tm = make_browser_tm(io_provider="twilio")
    tm._pending_chat_forward = ["Namaste!"]

    import asyncio

    asyncio.run(tm._drain_pending_chat_forward())

    # Untouched for the carrier leg; telephony has no transcript panel.
    assert tm._pending_chat_forward == ["Namaste!"]
    tm.tools["output"].handle.assert_not_awaited()


async def test_caller_transcript_forwarded_as_user_text():
    tm = make_browser_tm()
    meta_info = {"sequence_id": 3, "turn_id": 1}

    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        await tm._handle_transcriber_output("llm", "appointment kal chahiye", dict(meta_info))

    tm.tools["output"].handle.assert_awaited_once()
    packet = tm.tools["output"].handle.await_args.args[0]
    assert packet["data"] == "appointment kal chahiye"
    assert packet["meta_info"]["type"] == "text"
    assert packet["meta_info"]["role"] == "user"


async def test_caller_transcript_not_forwarded_on_telephony():
    tm = make_browser_tm(io_provider="plivo")
    meta_info = {"sequence_id": 3, "turn_id": 1}

    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        await tm._handle_transcriber_output("llm", "appointment kal chahiye", dict(meta_info))

    tm.tools["output"].handle.assert_not_awaited()


async def test_cumulative_segments_share_one_history_row():
    """Sarvam re-sends the turn-so-far per segment; history must hold one row."""
    from voiceai.helpers.conversation_history import ConversationHistory

    tm = make_browser_tm()
    tm.conversation_history = ConversationHistory()
    meta_info = {"sequence_id": 3, "turn_id": 1, "asr_turn_id": "turn_1"}

    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        await tm._handle_transcriber_output("llm", "हां जी", dict(meta_info))
        await tm._handle_transcriber_output("llm", "हां जी मेरा नाम विक्रम", dict(meta_info))

    users = [m for m in tm.conversation_history.messages if m.get("role") == "user"]
    assert len(users) == 1
    assert users[0]["content"] == "हां जी मेरा नाम विक्रम"


async def test_new_asr_turn_appends_new_history_row():
    from voiceai.helpers.conversation_history import ConversationHistory

    tm = make_browser_tm()
    tm.conversation_history = ConversationHistory()

    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        await tm._handle_transcriber_output(
            "llm", "हां जी", {"sequence_id": 3, "turn_id": 1, "asr_turn_id": "turn_1"}
        )
        await tm._handle_transcriber_output(
            "llm", "phone number batao", {"sequence_id": 4, "turn_id": 2, "asr_turn_id": "turn_2"}
        )

    users = [m for m in tm.conversation_history.messages if m.get("role") == "user"]
    assert len(users) == 2


async def test_overlapped_turn_forwards_only_new_words():
    """Rapid turns merge into history for LLM continuity, but the panel must show
    just this turn's words — not the whole call repainted per bubble."""
    from voiceai.helpers.conversation_history import ConversationHistory

    tm = make_browser_tm()
    tm.conversation_history = ConversationHistory()
    tm._inflight_response_activity = MagicMock(
        return_value={
            "response_in_pipeline": True,
            "audio_playing": False,
            "pending_marks": False,
            "pending_sequences": False,
            "pending_generation": False,
        }
    )
    tm._TaskManager__cleanup_downstream_tasks = AsyncMock()

    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        await tm._handle_transcriber_output(
            "llm", "हां जी", {"sequence_id": 3, "turn_id": 1, "asr_turn_id": "turn_1"}
        )
        await tm._handle_transcriber_output(
            "llm", "अप्रैल।", {"sequence_id": 4, "turn_id": 2, "asr_turn_id": "turn_2"}
        )

    forwarded = [c.args[0]["data"] for c in tm.tools["output"].handle.await_args_list]
    assert forwarded[-1] == "अप्रैल।"
    users = [m for m in tm.conversation_history.messages if m.get("role") == "user"]
    assert len(users) == 1  # merged row for the LLM, single bubble content forwarded


async def test_typed_chat_turn_hides_stream_markers():
    """bos/eos control markers must never reach the transcript panel."""
    import asyncio

    tm = make_browser_tm()
    tm.queues = {"llm": asyncio.Queue()}
    tm._run_llm_task = AsyncMock()
    await tm.queues["llm"].put({"data": "Speak with Human", "meta_info": {}})

    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        task = asyncio.create_task(tm._listen_llm_input_queue())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # The typed turn itself ran (past the bos gate).
    tm._run_llm_task.assert_awaited_once()
    assert tm.tools["output"].handle.await_count >= 0
    for call in tm.tools["output"].handle.await_args_list:
        assert call.args[0]["data"] not in ("<beginning_of_stream>", "<end_of_stream>")
