"""Transcriber base: queue contract, latency ledger and socket close (spec 0004, B12c)."""

import json
import time
import uuid
from typing import Any

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME

load_dotenv()
logger = get_logger(MODULE_NAME)


class BaseTranscriber:
    """Transcriber base: queue contract, latency ledger and socket close."""

    def __init__(self, input_queue: Any = None) -> None:
        self.input_queue = input_queue
        self.connection_on = True
        self.callee_speaking = False
        self.caller_speaking = False
        self.meta_info: dict[str, Any] | None = None
        self.transcription_start_time = 0
        self.last_vocal_frame_time = None
        self.previous_request_id: str | None = None
        self.current_request_id: str | None = None
        self.connection_time: float | None = None
        self.turn_latencies: list[dict[str, Any]] = []
        self.connection_error: str | None = None
        self.is_transcript_sent_for_processing = False

    def _upsert_turn_latency(self, entry: dict) -> None:
        """Replace existing turn_latencies entry with matching turn_id, or append if new."""
        # task_manager overwrites meta_info["turn_id"] with its own counter, so publish the ASR id separately.
        if entry.get("turn_id") is not None and isinstance(self.meta_info, dict):
            self.meta_info["asr_turn_id"] = entry["turn_id"]
        for i, t in enumerate(self.turn_latencies):
            if t.get("turn_id") == entry.get("turn_id"):
                self.turn_latencies[i] = entry
                return
        self.turn_latencies.append(entry)

    def update_meta_info(self) -> None:
        """Refresh the transcriber meta info."""
        self.meta_info["request_id"] = self.current_request_id if self.current_request_id else None
        self.meta_info["previous_request_id"] = self.previous_request_id
        self.meta_info["origin"] = "transcriber"

    @staticmethod
    def generate_request_id() -> Any:
        """Mint a request id."""
        return str(uuid.uuid4())

    async def signal_transcription_begin(self, msg: Any) -> Any:
        """Signal that transcription began."""
        send_begin_packet = False
        self.meta_info["request_id"] = self.current_request_id

        if not self.callee_speaking:
            self.callee_speaking = True
            logger.debug("Making callee speaking true")
            self.transcription_start_time = time.time() - msg["duration"]
            send_begin_packet = True

        return send_begin_packet

    async def log_latency_info(self) -> None:
        """Log turn latency details."""
        transcription_completion_time = time.time()
        if self.last_vocal_frame_time:
            logger.info(
                f"################ Time latency: For request {self.meta_info['request_id']}, user started speaking at {self.transcription_start_time}, last audio frame received at {self.last_vocal_frame_time} transcription_completed_at {transcription_completion_time} overall latency {transcription_completion_time - self.last_vocal_frame_time}"  # noqa: E501 — verbatim legacy line (R8)
            )
        else:
            logger.info(
                f"No confidence for the last vocal timeframe. Over transcription time {transcription_completion_time - self.transcription_start_time}"  # noqa: E501 — verbatim legacy line (R8)
            )

    async def _close(self, ws: Any, data: Any) -> None:
        if ws is None:
            # TEMP LOG
            logger.warning("Transcriber websocket already closed, skipping close message")
            return
        try:
            await ws.send(json.dumps(data))
        except Exception as e:
            logger.error(f"Error while closing transcriber stream {e}")

    async def cleanup(self) -> None:
        """Clean up transcriber resources. Override in subclasses."""
        pass

    def calculate_interim_to_final_latencies(self, interim_details: Any) -> Any:
        """Calculate time from first/last interim to final result."""
        if not interim_details:
            return None, None
        now = time.time()
        first_received_at = interim_details[0].get("received_at")
        last_received_at = interim_details[-1].get("received_at")
        first_interim_to_final_ms = round((now - first_received_at) * 1000, 2) if first_received_at else None
        last_interim_to_final_ms = round((now - last_received_at) * 1000, 2) if last_received_at else None
        return first_interim_to_final_ms, last_interim_to_final_ms
