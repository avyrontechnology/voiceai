"""Telephony output must skip non-audio (transcript/text) packets.

S2S transcript packets (data=str) share the output queue with audio. The
telephony handler used to feed them to audioop.lin2ulaw -> TypeError ->
latched _closed -> every later reply dropped with no trace (greeting heard
because its transcript arrives after its audio). Non-audio packets must be
skipped while audio keeps flowing on the same handler.
"""

import asyncio
import base64
from unittest.mock import MagicMock

from voiceai.output_handlers.telephony_providers.talko import TalkoOutputHandler


class _CollectSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, data: str):
        self.sent.append(data)


def _audio_meta(**overrides):
    meta = {
        "type": "audio",
        "format": "mulaw",
        "sequence_id": 5,
        "turn_id": 1,
        "response_uid": "r1",
        "response_group_uid": "g1",
        "message_category": "",
        "text_synthesized": "",
        "is_first_chunk": True,
        "end_of_llm_stream": False,
        "end_of_synthesizer_stream": False,
        "mark_id": "",
    }
    meta.update(overrides)
    return meta


async def test_text_packet_skipped_without_closing_handler():
    ws = _CollectSocket()
    handler = TalkoOutputHandler(
        websocket=ws, mark_event_meta_data=MagicMock(), log_dir_name=None
    )
    await handler.set_stream_sid("S1")
    # Agent + user transcript packets as the S2S loop emits them.
    await handler.handle(
        {"data": "Good day! Thank you for calling.", "meta_info": {"type": "text", "role": "agent"}}
    )
    await handler.handle(
        {"data": "Hindi mein baat kar sakte ho?", "meta_info": {"type": "text", "role": "user"}}
    )
    assert handler._closed is False
    assert ws.sent == []


async def test_audio_still_flows_after_text_packets():
    ws = _CollectSocket()
    handler = TalkoOutputHandler(
        websocket=ws, mark_event_meta_data=MagicMock(), log_dir_name=None
    )
    await handler.set_stream_sid("S1")
    await handler.handle(
        {"data": "transcript that used to kill output", "meta_info": {"type": "text"}}
    )
    await handler.handle(
        {"data": b"\xaa" * 160, "meta_info": _audio_meta()}
    )
    assert handler._closed is False
    assert len(ws.sent) == 3  # pre-mark + media + post-mark
