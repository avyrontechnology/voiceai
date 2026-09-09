"""WB-3c: TALK-DESPITE-SILENCE for the S2S leg.

(i) S2S output progresses ack-independently: with total_acked==0 the output loop
still advances and turns close on EOS/sentinel via the playout estimate, while
total_missed>0 is logged (mark silence must never mute the call).
(ii) The Talko mulaw-8k ingest contract is verified (decode + resample to the
provider rate, bytes/frame logged once); the tolerant-input rescue stays intact.
"""

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager import task_manager as tm_module
from voiceai.agent_manager.task_manager import TaskManager
from voiceai.enums import TelephonyProvider
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData
from voiceai.helpers.utils import pcm_to_ulaw
from voiceai.input_handlers.telephony_providers.talko import TalkoInputHandler
from voiceai.input_handlers.telephony_providers.twilio import TwilioInputHandler
from voiceai.s2s import events as s2s_events


def _silence_pcm(samples=480):
    return b"\x00\x00" * samples


def make_tm(*, io_provider="talko", web=False, in_rate=16000, out_rate=24000):
    tm = TaskManager.__new__(TaskManager)
    tm.task_id = 0
    tm.run_id = "exec-123"
    tm.turn_based_conversation = False
    tm.is_web_based_call = web
    tm.default_io = io_provider == "default"
    tm.conversation_ended = False
    tm.should_record = False
    tm.has_transfer = False
    tm.hangup_triggered = False
    tm._end_call_in_progress = False
    tm.hangup_detail = None
    tm.on_turn_usage = None
    tm.on_provider_health = None
    tm.dtmf_events = []
    tm.conversation_start_init_ts = 0
    tm.s2s_provider_name = "openai_realtime"
    tm.s2s_model = "gpt-realtime-2.1"
    tm.buffered_output_queue = asyncio.Queue()
    tm.audio_queue = asyncio.Queue()
    tm.queues = {"dtmf": asyncio.Queue()}
    tm.conversation_history = MagicMock()
    tm.kwargs = {"api_tools": {"tools_params": {}}}
    tm.task_config = {"tools_config": {"input": {"provider": io_provider}}}
    tm._s2s_tool_tasks = set()
    tm._s2s_hangup_after_response = False
    tm._s2s_started_at = 0
    tm._s2s_welcome_gate_ms = 0
    tm._s2s_welcome_sent = False
    tm._s2s_agent_speaking = False
    tm._s2s_turn_seq = 0
    tm._s2s_playout_until = 0.0
    tm.interruption_manager = MagicMock()
    tm.last_transmitted_timestamp = 0
    tm.time_since_last_spoken_human_word = 0
    tm.user_spoke = False
    tm.call_hangup_message_config = None
    tm.mark_event_meta_data = MarkEventMetaData()

    provider = SimpleNamespace(
        input_sample_rate=in_rate,
        output_sample_rate=out_rate,
        send_audio=AsyncMock(),
        send_dtmf=AsyncMock(),
        send_function_result=AsyncMock(),
        commit_function_results=AsyncMock(),
        trigger_response=AsyncMock(),
    )
    output = SimpleNamespace(
        handle=AsyncMock(), handle_interruption=AsyncMock(), get_provider=MagicMock(return_value=io_provider)
    )
    inp = SimpleNamespace(io_provider=io_provider, update_is_audio_being_played=MagicMock(), is_dtmf_active=False)
    tm.tools = {"s2s": provider, "output": output, "input": inp}
    tm.sampling_rate = 8000 if tm._s2s_is_carrier_leg() else 24000
    tm._s2s_input = tm._s2s_input_format()
    tm._s2s_output = tm._s2s_output_format()
    return tm


class TestTalkoEncodingContract:
    def test_talko_leg_is_mulaw_8k(self):
        tm = make_tm(io_provider="talko")
        assert tm._s2s_telephony_provider() == "talko"
        assert tm._s2s_input.encoding is s2s_events.AudioEncoding.MULAW
        assert tm._s2s_input.sample_rate == 8000

    def test_mulaw_8k_in_yields_expected_16k_pcm_length(self):
        # 160 mu-law bytes (20ms @8k) -> 160 PCM samples -> 320 samples @16k -> 640 bytes.
        tm = make_tm(io_provider="talko", in_rate=16000)
        out = tm._s2s_encode_input(pcm_to_ulaw(_silence_pcm(160)))
        assert len(out) == 640

    def test_mulaw_8k_in_yields_expected_24k_pcm_length(self):
        # Same frame at a 24k provider: 160 samples @8k -> 480 samples @24k -> 960 bytes.
        tm = make_tm(io_provider="talko", in_rate=24000)
        out = tm._s2s_encode_input(pcm_to_ulaw(_silence_pcm(160)))
        assert len(out) == 960

    def test_encode_logs_bytes_per_frame_once(self, caplog):
        tm = make_tm(io_provider="talko", in_rate=16000)
        frame = pcm_to_ulaw(_silence_pcm(160))
        with caplog.at_level(logging.INFO, logger="voiceai.agent_manager.task_manager"):
            tm._s2s_encode_input(frame)
            tm._s2s_encode_input(frame)
        lines = [r.getMessage() for r in caplog.records if "S2S ingest encode" in r.getMessage()]
        assert len(lines) == 1
        assert "in_bytes=160" in lines[0]

    async def test_ingest_uses_the_encode_contract(self):
        tm = make_tm(io_provider="talko", in_rate=16000)
        await tm.audio_queue.put({"data": pcm_to_ulaw(_silence_pcm(160)), "meta_info": {}})
        await tm.audio_queue.put({"data": None, "meta_info": {"eos": True}})
        await tm._s2s_audio_ingest_loop()
        assert len(tm.tools["s2s"].send_audio.await_args.args[0]) == 640

    def test_talko_tolerant_input_rescue_intact(self):
        # The rescue must survive this change: Talko keeps its own
        # non-telephony handler (bare media / flat audio / implicit start).
        assert TalkoInputHandler._handle_non_telephony_packet is not TwilioInputHandler._handle_non_telephony_packet
        assert TelephonyProvider.TALKO.value in TelephonyProvider.mulaw_values()


class TestAckIndependentProgress:
    def test_mark_progress_zeros_when_marks_silent(self):
        tm = make_tm()
        assert tm._s2s_mark_progress() == {"total_sent": 0, "total_acked": 0, "total_missed": 0}

    async def test_acked_zero_still_closes_turn_on_eos(self, caplog):
        tm = make_tm()
        # One chunk sent, zero acks: the relay went silent.
        tm.mark_event_meta_data.update_data(
            "mark-1",
            {
                "type": "audio",
                "sequence_id": -1,
                "turn_id": 0,
                "text_synthesized": "hello",
                "duration": 0.5,
                "sent_ts": time.time(),
            },
        )
        assert tm._s2s_mark_progress()["total_missed"] == 1

        with caplog.at_level(logging.WARNING, logger="voiceai.agent_manager.task_manager"):
            await tm._s2s_finish_turn(s2s_events.ResponseDone(transcript="", usage=None))

        # The turn still closes on EOS/sentinel instead of muting the call...
        assert tm._s2s_turn_seq == 1
        sentinel = await asyncio.wait_for(tm.buffered_output_queue.get(), timeout=2)
        assert sentinel["data"] == b"\x00"
        assert sentinel["meta_info"].get("end_of_synthesizer_stream") is True
        # ...while total_missed>0 is logged loudly.
        assert "total_missed=1" in "\n".join(r.getMessage() for r in caplog.records)

    async def test_output_loop_advances_with_zero_acks(self, caplog):
        tm = make_tm()
        audio = {"data": b"\x01\x02" * 80, "meta_info": tm._s2s_meta()}
        sentinel = {
            "data": b"\x00",
            "meta_info": tm._s2s_meta(end_of_llm_stream=True, end_of_synthesizer_stream=True),
        }
        await tm.buffered_output_queue.put(audio)
        await tm.buffered_output_queue.put(sentinel)

        async def run_until_drained():
            while tm.tools["output"].handle.await_count < 2:
                await asyncio.sleep(0.01)
            tm.conversation_ended = True

        driver = asyncio.create_task(run_until_drained())
        with caplog.at_level(logging.WARNING, logger="voiceai.agent_manager.task_manager"):
            await asyncio.wait_for(tm._s2s_output_loop(), timeout=5)
        await driver

        # Audio flows while total_missed is only logged — never gated on acks.
        assert tm.tools["output"].handle.await_count == 2
        assert tm.tools["input"].update_is_audio_being_played.called

    async def test_talko_mark_round_trip_echo_then_silent(self):
        # Echo path: the relay returns the mark -> acked>0, turn closes talking.
        tm = make_tm()
        tm.mark_event_meta_data.update_data(
            "mark-echo",
            {
                "type": "audio",
                "sequence_id": -1,
                "turn_id": 0,
                "text_synthesized": "hello",
                "duration": 0.5,
                "sent_ts": time.time(),
            },
        )
        fetched = tm.mark_event_meta_data.fetch_data("mark-echo")
        assert fetched
        tm.mark_event_meta_data.record_ack(0.05, fetched.get("sequence_id"))
        assert tm._s2s_mark_progress()["total_acked"] == 1

        await tm._s2s_finish_turn(s2s_events.ResponseDone(transcript="", usage=None))
        assert tm._s2s_turn_seq == 1

        # Silent path: no marks at all -> still talks (turn closes on sentinel).
        tm2 = make_tm()
        await tm2._s2s_finish_turn(s2s_events.ResponseDone(transcript="", usage=None))
        assert tm2._s2s_turn_seq == 1
        sentinel = await asyncio.wait_for(tm2.buffered_output_queue.get(), timeout=2)
        assert sentinel["data"] == b"\x00"

    def test_ack_independent_flag_defaults_safe(self, monkeypatch):
        monkeypatch.delenv("S2S_ACK_INDEPENDENT", raising=False)
        assert tm_module._s2s_ack_independent() is True
        monkeypatch.setenv("S2S_ACK_INDEPENDENT", "0")
        assert tm_module._s2s_ack_independent() is False

    def test_talko_encoding_env_defaults_safe(self, monkeypatch):
        monkeypatch.delenv("S2S_TALKO_ENCODING", raising=False)
        assert tm_module._s2s_talko_encoding() == "mulaw-8k"
