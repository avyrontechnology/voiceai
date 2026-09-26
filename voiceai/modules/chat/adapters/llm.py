"""Conversational single-turn completion over LiteLLM (spec 0038, Phase C).

One-shot `messages` in, text out — the chat service passes full history
inline every turn, so no sessionful client is needed. Imported at call time
on purpose (the agents-adapter precedent): `voiceai.llms` drags litellm in,
and importing the chat package must stay lightweight.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["CHAT_DEFAULT_MODEL", "complete_chat_turn"]

#: Fallback model when the agent row names none (operator-overridable).
CHAT_DEFAULT_MODEL: Final[str] = "gpt-4o-mini"


async def complete_chat_turn(messages: list[dict[str, Any]], model: str | None = None) -> str:
    """Complete one chat turn through LiteLLM.

    Args:
        messages: Full history (system first, alternating user/assistant).
        model: Provider model id, or `None` for the default.

    Returns:
        The assistant text.
    """
    from voiceai.llms.litellm import LiteLLM  # §3.1 bridge import — retires with the llms migration spec

    llm = LiteLLM(model=model or CHAT_DEFAULT_MODEL, max_tokens=1000)
    answer = await llm.generate(messages=messages)
    return str(answer)
