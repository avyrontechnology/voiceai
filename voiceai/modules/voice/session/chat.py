"""Browser-leg chat forwarding: transcript lines to the Live Talk panel (spec 0032).

Verbatim move of `TaskManager._forward_browser_text` /
`_drain_pending_chat_forward`: browser legs only (telephony has no transcript
panel), never-raises sends, per-turn bubble updates via `asr_turn_id`, and a
bounded recent-set deduplicating eager speculative re-emissions. Logger
channel moves to `otobaai.voice` per the move discipline; every message
string is verbatim.
"""

from __future__ import annotations

from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.function_runtime import create_ws_data_packet
from voiceai.modules.voice.constants import MODULE_NAME

__all__ = ["ChatSession", "drain_pending_chat_forward", "forward_browser_text"]

logger = get_logger(MODULE_NAME)


class ChatSession(Protocol):
    """The session surface the forwarding bodies touch (duck-typed at runtime)."""

    tools: Any
    _forwarded_chat_texts: Any
    _pending_chat_forward: Any

    def _is_browser_leg(self) -> bool: ...


async def forward_browser_text(session: ChatSession, text: Any, role: Any, asr_turn_id: Any = None) -> None:
    """Forward one transcript line to the Live Talk / chat panel.

    Browser legs only; telephony has no transcript panel and the dashboard
    flow ignores these frames. Never raises — a transcript must not kill a call.
    asr_turn_id lets the panel update one bubble per caller turn instead of
    appending every cumulative re-emission.

    Args:
        session: The live call session (duck-typed `ChatSession`).
        text: The transcript line (blank lines are no-ops).
        role: Speaker role for the panel bubble.
        asr_turn_id: Caller turn id for bubble updates.
    """
    if not text or not str(text).strip():
        return
    if not session._is_browser_leg():
        return
    # Bounded recent-set: eager speculative turns and the confirming real turn
    # stage identical text (also survives __new__-built managers in tests).
    sent = getattr(session, "_forwarded_chat_texts", None)
    if sent is None:
        sent = session._forwarded_chat_texts = []
    if str(text).strip() in sent:
        return
    output = (session.tools or {}).get("output")
    if output is None or getattr(output, "handle", None) is None:
        return
    packet = create_ws_data_packet(
        str(text), {"type": "text", "role": role, "sequence_id": -1, "asr_turn_id": asr_turn_id}
    )
    try:
        await output.handle(packet)
    except Exception as e:
        logger.debug(f"Browser transcript forward failed: {e}")
        return
    logger.info(f"Browser-leg chat reply forwarded | role={role} chars={len(str(text))}")
    sent.append(str(text).strip())
    del sent[:-50]


async def drain_pending_chat_forward(session: ChatSession) -> None:
    """Flush staged agent replies to the browser transcript panel.

    Args:
        session: The live call session (duck-typed `ChatSession`).
    """
    if not session._is_browser_leg():
        return
    pending = getattr(session, "_pending_chat_forward", None) or []
    session._pending_chat_forward = []
    for text in pending:
        await forward_browser_text(session, text, "agent")
