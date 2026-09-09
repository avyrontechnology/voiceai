import asyncio
import json
import os
import audioop
import time
import uuid
from dotenv import load_dotenv
from .default import DefaultOutputHandler
from voiceai.constants import AUDIO_STREAM_END_SENTINELS
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.utils import calculate_audio_duration

logger = configure_logger(__name__)
load_dotenv()

# A carrier media socket can go half-dead (TCP stops delivering ACKs, no close
# frame ever arrives) without raising — a bare `websocket.send_text()` then
# never returns. Bound every send so a dead socket fails fast instead of
# freezing the caller (e.g. __cleanup_downstream_tasks) forever.
OUTPUT_SEND_TIMEOUT_S = float(os.getenv("OUTPUT_SEND_TIMEOUT_S", "5"))

_odd_pcm_chunks = 0


def lin16_to_mulaw(pcm):
    """audioop.lin2ulaw over 16-bit PCM, dropping a trailing odd byte instead of raising.

    A synthesizer or resampler occasionally hands over a chunk that is not a whole
    number of 16-bit frames; audioop then raises "not a whole number of frames" and
    that single chunk used to mute the rest of the call. Half a sample is inaudible.
    """
    global _odd_pcm_chunks
    if len(pcm) % 2:
        _odd_pcm_chunks += 1
        log = logger.warning if _odd_pcm_chunks == 1 or _odd_pcm_chunks % 500 == 0 else logger.debug
        log(
            "dropping trailing odd byte from %d-byte PCM chunk before mulaw encode (occurrence %d)",
            len(pcm),
            _odd_pcm_chunks,
        )
        pcm = pcm[:-1]
    return audioop.lin2ulaw(pcm, 2)


class TelephonyOutputHandler(DefaultOutputHandler):
    error_component = "telephony"

    def __init__(self, io_provider, websocket=None, mark_event_meta_data=None, log_dir_name=None):
        super().__init__(io_provider, websocket, log_dir_name, mark_event_meta_data=mark_event_meta_data)
        self.mark_event_meta_data = mark_event_meta_data

        self.stream_sid = None
        self.current_request_id = None
        self.rejected_request_ids = set()

    def _send_timeout(self):
        # Read at call time so deployments (env) and tests (monkeypatch) can tune it.
        return OUTPUT_SEND_TIMEOUT_S

    async def handle_interruption(self):
        pass

    async def form_media_message(self, audio_data, audio_format):
        pass

    async def form_mark_message(self, mark_id):
        pass

    async def set_stream_sid(self, stream_id):
        self.stream_sid = stream_id

    async def handle(self, ws_data_packet):
        if self._closed:
            # Never swallow this silently: a latched-closed handler drops
            # every subsequent packet with no other trace, which looks
            # exactly like "the agent stopped talking" mid-call.
            logger.warning(
                "%s output handler is closed, dropping %s packet",
                self.io_provider,
                (ws_data_packet or {}).get("meta_info", {}).get("type", "?"),
            )
            return
        try:
            audio_chunk = (ws_data_packet or {}).get("data")
            meta_info = (ws_data_packet or {}).get("meta_info") or {}
            # Telephony legs carry audio only — transcript/text packets share
            # this queue (S2S agent/user transcripts) and must NOT reach the
            # audio encoder: treating their str payload as PCM raised
            # TypeError inside lin2ulaw and latched the handler closed,
            # muting every later reply with no trace. Skip them loudly.
            if meta_info.get("type") not in ("audio", None):
                logger.info(
                    "%s output handler skipping non-audio packet type=%s",
                    self.io_provider,
                    meta_info.get("type"),
                )
                return
            if self.stream_sid is None:
                self.stream_sid = meta_info.get("stream_sid", None)

            if not audio_chunk:
                # None (no data at all) or b"" (a synthesizer hiccup): nothing to encode
                # and nothing to mark. The end-of-stream sentinels are non-empty and take
                # the mark-only path below.
                logger.warning(
                    "%s output handler skipping %s audio packet",
                    self.io_provider,
                    "missing" if audio_chunk is None else "empty",
                )
                return

            if len(audio_chunk) == 1:
                audio_chunk += b"\x00"

            if audio_chunk and self.stream_sid and len(audio_chunk) != 1:
                if audio_chunk != b"\x00\x00":
                    audio_format = meta_info.get("format", "wav")

                    # sending of pre-mark message
                    pre_mark_event_meta_data = {
                        "type": "pre_mark_message",
                        "sequence_id": meta_info.get("sequence_id"),
                        "turn_id": meta_info.get("turn_id"),
                        "response_uid": meta_info.get("response_uid"),
                        "response_group_uid": meta_info.get("response_group_uid"),
                    }
                    mark_id = str(uuid.uuid4())
                    self.mark_event_meta_data.update_data(mark_id, pre_mark_event_meta_data)
                    if (
                        meta_info.get("message_category") == "agent_welcome_message"
                        and self.mark_event_meta_data.welcome_pre_mark_id is None
                    ):
                        self.mark_event_meta_data.welcome_pre_mark_id = mark_id
                    logger.info(
                        "VOICEAI_TRACE_TEL send_pre_mark mark_id=%s seq=%s turn=%s response_uid=%s group_uid=%s category=%s",
                        mark_id,
                        meta_info.get("sequence_id"),
                        meta_info.get("turn_id"),
                        meta_info.get("response_uid"),
                        meta_info.get("response_group_uid"),
                        meta_info.get("message_category", ""),
                    )
                    mark_message = await self.form_mark_message(mark_id)
                    await self._send_text(json.dumps(mark_message))

                    # sending of audio chunk
                    if (
                        audio_format == "pcm"
                        and meta_info.get("message_category", "") == "agent_welcome_message"
                        and self.io_provider in ("plivo", "vobiz")
                        and meta_info.get("cached") is True
                    ):
                        audio_format = "wav"
                    media_message = await self.form_media_message(audio_chunk, audio_format)
                    await self._send_text(json.dumps(media_message))
                    if (
                        meta_info.get("message_category", "") == "agent_welcome_message"
                        and not self.welcome_message_sent_ts
                    ):
                        self.welcome_message_sent_ts = time.time() * 1000
                    logger.info(f"Sending media event - {meta_info.get('mark_id')}")

                # Telephony streams at 8k. The end-of-stream sentinel sent no media above,
                # so it adds no playback time and must not advance the playout estimate.
                duration = (
                    0
                    if audio_chunk in AUDIO_STREAM_END_SENTINELS
                    else calculate_audio_duration(len(audio_chunk), 8000, format=meta_info.get("format", "mulaw"))
                )
                mark_event_meta_data = {
                    "text_synthesized": ""
                    if meta_info.get("sequence_id") == -1
                    else meta_info.get("text_synthesized", ""),
                    "type": meta_info.get("message_category", ""),
                    "is_first_chunk": meta_info.get("is_first_chunk", False),
                    "is_final_chunk": meta_info.get("end_of_llm_stream", False)
                    and meta_info.get("end_of_synthesizer_stream", False),
                    "sequence_id": meta_info.get("sequence_id"),
                    "turn_id": meta_info.get("turn_id"),
                    "response_uid": meta_info.get("response_uid"),
                    "response_group_uid": meta_info.get("response_group_uid"),
                    "duration": duration,
                    "sent_ts": time.time(),  # Track when audio was actually sent to telephony provider
                }
                mark_id = (
                    meta_info.get("mark_id")
                    if (meta_info.get("mark_id") and meta_info.get("mark_id") != "")
                    else str(uuid.uuid4())
                )
                # sending of post-mark message
                self.mark_event_meta_data.update_data(mark_id, mark_event_meta_data)
                logger.info(
                    "VOICEAI_TRACE_TEL send_post_mark mark_id=%s seq=%s turn=%s response_uid=%s group_uid=%s final=%s category=%s text_len=%s",
                    mark_id,
                    meta_info.get("sequence_id"),
                    meta_info.get("turn_id"),
                    meta_info.get("response_uid"),
                    meta_info.get("response_group_uid"),
                    mark_event_meta_data.get("is_final_chunk"),
                    meta_info.get("message_category", ""),
                    len(mark_event_meta_data.get("text_synthesized", "") or ""),
                )
                mark_message = await self.form_mark_message(mark_id)
                await self._send_text(json.dumps(mark_message))
            else:
                logger.info("Not sending")
        except Exception as e:
            # Latch closed ONLY when the socket is gone (WebSocketDisconnect, ConnectionClosed,
            # Starlette's closed-socket RuntimeError). A send timeout is a transient stall and
            # anything else (odd-length PCM in lin2ulaw, a missing meta key, ...) is a fault in
            # this one packet: drop it, log it once per error type, keep the socket open.
            self._on_send_error(e, "packet")
