import base64
import binascii
from typing import Any

from voiceai.input_handlers.telephony_providers.twilio import TwilioInputHandler
from dotenv import load_dotenv
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)
load_dotenv()


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


class TalkoInputHandler(TwilioInputHandler):
    """Talko (Tata Tele via talko-service) input handler.

    Preferred shape is Twilio-style events (``start`` with callSid/streamSid,
    ``media`` with payload+chunk+timestamp, ``mark`` acks, ``stop``) relayed by
    talko-service ``voiceai_relay.py`` — the Twilio implementation applies
    verbatim and only the provider tag differs for routing/observability.

    The relay has shipped frames without an ``event`` key (see the
    "ignoring non-telephony frame" flood that starved stream_sid and killed
    the call after the 10s timeout), so this handler additionally rescues:

    - ``{type: audio, data: <b64>}`` browser-style audio (misrouted leg or
      relay passthrough) — ingested directly, minting a synthetic stream_sid
      when no start ever arrived so the greeting is not skipped.
    - Bare ``{media: {payload, timestamp}}`` without the event wrapper.
    - Top-level id fields (``call_id``/``callSid``/..., ``stream_id``/...) as
      an implicit start when the ``start`` envelope is missing.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.io_provider = "talko"

    async def call_start(self, packet: dict) -> None:
        start = packet.get("start") if isinstance(packet, dict) else None
        if not isinstance(start, dict):
            start = packet if isinstance(packet, dict) else {}
        self.call_sid = _first_present(
            start.get("callSid"),
            start.get("call_sid"),
            start.get("callId"),
            start.get("call_id"),
            packet.get("callSid") if isinstance(packet, dict) else None,
            packet.get("call_sid") if isinstance(packet, dict) else None,
            packet.get("call_id") if isinstance(packet, dict) else None,
            self.call_sid,
        )
        self.stream_sid = _first_present(
            start.get("streamSid"),
            start.get("stream_sid"),
            start.get("streamId"),
            start.get("stream_id"),
            packet.get("streamSid") if isinstance(packet, dict) else None,
            packet.get("stream_sid") if isinstance(packet, dict) else None,
            packet.get("stream_id") if isinstance(packet, dict) else None,
            self.stream_sid,
        )
        if self.stream_sid is None:
            logger.warning(f"talko receiver got start without stream id: {str(packet)[:300]!r}")

    async def _handle_non_telephony_packet(self, packet: Any, raw_message: str) -> bool:
        if await super()._handle_non_telephony_packet(packet, raw_message):
            return True
        if not isinstance(packet, dict):
            return False

        # Implicit start: relay sent ids without the Twilio {"event": "start"} envelope.
        # Adopt them first, then fall through to audio handling below so a frame
        # carrying BOTH ids and audio (e.g. Tata-style {call_id, audio}) sets the
        # ids AND flows audio instead of only setting ids and starving.
        call_id = _first_present(
            packet.get("callSid"), packet.get("call_sid"), packet.get("callId"), packet.get("call_id")
        )
        stream_id = _first_present(
            packet.get("streamSid"), packet.get("stream_sid"), packet.get("streamId"), packet.get("stream_id")
        )
        nested_start = packet.get("start") if isinstance(packet.get("start"), dict) else None
        saw_ids = nested_start is not None or call_id is not None or stream_id is not None
        if saw_ids:
            await self.call_start(packet)

        # Bare media without the event wrapper: {media: {payload, timestamp}}.
        media = packet.get("media")
        if isinstance(media, dict) and isinstance(media.get("payload"), str):
            try:
                audio = base64.b64decode(media["payload"])
            except (binascii.Error, ValueError) as e:
                logger.warning(f"talko receiver dropping undecodable bare-media frame: {e}")
                return True
            if not audio:
                return True
            await self._ensure_stream_sid("bare media")
            meta_info = {
                "io": self.io_provider,
                "call_sid": self.call_sid,
                "stream_sid": self.stream_sid,
                "sequence": (self.input_types or {}).get("audio", 0),
            }
            await self.ingest_audio(audio, meta_info)
            return True

        # Text frames are never audio, even when their payload happens to be
        # base64-decodable — ingesting them would inject garbage into the STT.
        if packet.get("type") == "text":
            return saw_ids
        # Flat audio fields some relays emit: {audio/data/payload: <b64>}.
        # Only when no explicit type claims the frame (typed non-audio frames
        # such as mark/clear must stay ignored, not misread as audio).
        if packet.get("type") is not None and packet.get("type") != "audio":
            return saw_ids
        for key in ("audio", "data", "payload"):
            value = packet.get(key)
            if isinstance(value, str) and len(value) >= 32:
                try:
                    audio = base64.b64decode(value, validate=True)
                except (binascii.Error, ValueError):
                    continue
                if len(audio) < 40:
                    continue
                await self._ensure_stream_sid(f"flat {key}")
                meta_info = {
                    "io": self.io_provider,
                    "call_sid": self.call_sid,
                    "stream_sid": self.stream_sid,
                    "sequence": (self.input_types or {}).get("audio", 0),
                }
                await self.ingest_audio(audio, meta_info)
                return True
        return saw_ids
