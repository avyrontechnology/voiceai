"""The simple streaming conversational brain (spec 0002, A6).

Behavior-preserving verbatim move of `voiceai/agent_types/contextual_conversational_agent.py`:
a passthrough over the composed LLM's stream plus two side judgments — "should we hang
up?" and "is this a voicemail system?" — each running on its own env-selected OpenAI
model and degrading to a safe default on ANY failure.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator
from typing import Any, Final

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.llms.openai_llm import OpenAiLLM
from voiceai.modules.agents.adapters.brains import VOICEMAIL_DETECTION_PROMPT, format_messages
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.constants import (
    DEFAULT_VOICEMAIL_DETECTION_MODEL,
    ENV_CHECK_FOR_COMPLETION_LLM,
    ENV_VOICEMAIL_DETECTION_LLM,
    HANGUP_KEY,
    IS_VOICEMAIL_KEY,
    LATENCY_MS_KEY,
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    NEGATIVE_ANSWER,
    SYSTEM_ROLE,
    USER_ROLE,
    VOICEMAIL_USER_MESSAGE_TEMPLATE,
)

__all__ = ["StreamingContextualAgent"]

# legacy-parity(spec-0002): the legacy module loaded .env at import time; kept until
# composition owns configuration. TODO(spec-0004): retire with the environment migration.
load_dotenv()
logger = get_logger(MODULE_NAME)

#: Milliseconds per second — the legacy latency arithmetic, promoted from the inline 1000.
_MS_PER_SECOND: Final = 1000


class StreamingContextualAgent(BaseAgent):
    """Streams the composed LLM and runs the completion/voicemail side judgments.

    Args:
        llm: The conversation LLM (duck-typed legacy provider: `generate_stream`, `model`).
    """

    def __init__(self, llm: Any) -> None:  # why: the LLM is the legacy duck-typed provider seam
        super().__init__()
        self.llm = llm
        # TODO(spec-0004): direct env reads are verbatim legacy behavior; they move to
        # `core.environment` when the voice runtime composes the judgment models.
        self.conversation_completion_llm = OpenAiLLM(model=os.getenv(ENV_CHECK_FOR_COMPLETION_LLM, llm.model))
        self.voicemail_llm = OpenAiLLM(model=os.getenv(ENV_VOICEMAIL_DETECTION_LLM, DEFAULT_VOICEMAIL_DETECTION_MODEL))
        self.history: list[dict[str, str]] = [{MESSAGE_CONTENT_KEY: ""}]

    async def check_for_completion(
        self,
        messages: list[dict[str, Any]],  # why: free-form legacy chat messages
        check_for_completion_prompt: str,
        meta_info: dict[str, Any] | None = None,  # why: the engine's free-form call metadata
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Ask the judgment LLM whether the conversation should end.

        The judge answers JSON; its metadata gains a ``latency_ms`` measurement. Any
        failure — LLM error or unparseable JSON — degrades to "keep talking".

        Args:
            messages: Conversation history, formatted into the user turn.
            check_for_completion_prompt: The system prompt driving the judgment.
            meta_info: Engine call metadata forwarded to the LLM.

        Returns:
            ``(judgment, metadata)`` — on failure ``({"hangup": "No"}, {})``.
        """
        try:
            prompt = [
                {MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: check_for_completion_prompt},
                {MESSAGE_ROLE_KEY: USER_ROLE, MESSAGE_CONTENT_KEY: format_messages(messages)},
            ]

            start_time = time.time()
            response, metadata = await self.conversation_completion_llm.generate(
                prompt, request_json=True, ret_metadata=True, meta_info=meta_info
            )
            latency_ms = (time.time() - start_time) * _MS_PER_SECOND

            hangup = json.loads(response)
            metadata[LATENCY_MS_KEY] = latency_ms

            return hangup, metadata
        except Exception as e:  # legacy-parity(spec-0002): any failure means "keep talking"
            logger.error(f"check_for_completion exception: {e}")
            return {HANGUP_KEY: NEGATIVE_ANSWER}, {}

    async def check_for_voicemail(
        self,
        user_message: str,
        voicemail_detection_prompt: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Check whether the user message indicates a voicemail system.

        Args:
            user_message: The transcribed message from the user.
            voicemail_detection_prompt: Custom detection prompt; the packaged default
                otherwise.

        Returns:
            ``(judgment, metadata)`` with ``latency_ms`` added — on failure
            ``({"is_voicemail": "No"}, {})``.
        """
        try:
            detection_prompt = voicemail_detection_prompt or VOICEMAIL_DETECTION_PROMPT
            prompt = [
                {MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: detection_prompt},
                {
                    MESSAGE_ROLE_KEY: USER_ROLE,
                    MESSAGE_CONTENT_KEY: VOICEMAIL_USER_MESSAGE_TEMPLATE.format(user_message=user_message),
                },
            ]

            start_time = time.time()
            response, metadata = await self.voicemail_llm.generate(prompt, request_json=True, ret_metadata=True)
            latency_ms = (time.time() - start_time) * _MS_PER_SECOND

            result = json.loads(response)
            metadata[LATENCY_MS_KEY] = latency_ms
            return result, metadata
        except Exception as e:  # legacy-parity(spec-0002): any failure means "not a voicemail"
            logger.error(f"check_for_voicemail exception: {e}")
            return {IS_VOICEMAIL_KEY: NEGATIVE_ANSWER}, {}

    async def generate(
        self,
        history: list[dict[str, Any]],  # why: free-form legacy chat history
        synthesize: bool = False,
        meta_info: dict[str, Any] | None = None,  # why: the engine's free-form call metadata
    ) -> AsyncGenerator[Any, None]:  # why: yields whatever the provider stream yields
        """Stream the conversation LLM's tokens through unchanged.

        Args:
            history: Chat history handed to the provider stream.
            synthesize: Provider flag, forwarded verbatim.
            meta_info: Engine call metadata, forwarded verbatim.

        Yields:
            Each item of ``llm.generate_stream`` untouched.
        """
        async for token in self.llm.generate_stream(history, synthesize=synthesize, meta_info=meta_info):
            yield token
