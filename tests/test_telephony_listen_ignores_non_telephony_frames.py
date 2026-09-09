"""Telephony receiver must survive non-telephony frames.

Browser legs (playground Talk/Chat) speak {type}-frames. If such an agent is
configured with a telephony IO provider, the first {type:init} frame used to
raise KeyError('event') -> EOS pushed -> loop break -> dead input, skipped
greeting, total silence. Unknown shapes must be skipped, never fatal.
"""

import asyncio
import json
from unittest.mock import MagicMock

from voiceai.input_handlers.telephony_providers.twilio import TwilioInputHandler


class _ScriptSocket:
    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_text(self):
        return self._frames.pop(0)


async def test_non_telephony_frame_does_not_kill_receiver():
    transcriber_q: asyncio.Queue = asyncio.Queue()
    mark_meta = MagicMock()
    mark_meta.fetch_data.return_value = None
    handler = TwilioInputHandler(
        queues={"transcriber": transcriber_q},
        websocket=_ScriptSocket(
            [
                json.dumps({"type": "init", "meta_data": {"source": "ui-live-talk"}}),
                json.dumps({"event": "mark", "mark": {"name": "m-1"}}),
                json.dumps({"event": "stop"}),
            ]
        ),
        input_types={"audio": 0},
        mark_event_meta_data=mark_meta,
    )
    await asyncio.wait_for(handler._listen(), timeout=10)
    # The mark AFTER the unknown frame was still processed...
    mark_meta.fetch_data.assert_called_once_with("m-1")
    # ...and exactly one EOS (from the stop frame, not from a crash) arrived.
    assert transcriber_q.qsize() == 1
    eos = transcriber_q.get_nowait()
    assert eos.get("meta_info", {}).get("eos") is True
