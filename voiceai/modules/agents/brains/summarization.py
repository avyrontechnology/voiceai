"""The call-summary brain (spec 0002, A6).

Behavior-preserving verbatim move of `voiceai/agent_types/summarization_agent.py`: one
plain-text `generate` over the composed LLM, degrading to an empty summary on failure.
"""

from __future__ import annotations

from typing import Any, Final

from voiceai.common.logger import get_logger
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.constants import MODULE_NAME

__all__ = ["SummarizationContextualAgent"]

logger = get_logger(MODULE_NAME)

#: The single wire key of the summary answer.
_SUMMARY_KEY: Final[str] = "summary"


class SummarizationContextualAgent(BaseAgent):
    """Runs the summarization task's LLM once for a plain-text summary.

    Args:
        llm: The summarization LLM (duck-typed legacy provider: `generate`).
        prompt: Accepted and dropped (# legacy-parity(spec-0002): the legacy constructor
            never stored it; the engine passes the system prompt through the history).
    """

    def __init__(self, llm: Any, prompt: str | None = None) -> None:  # why: duck-typed provider seam
        super().__init__()
        self.llm = llm
        self.current_messages = 0
        self.is_inference_on = False
        self.has_intro_been_sent = False

    async def generate(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        """Answer ``{"summary": <text>}``, empty when the LLM fails (legacy parity).

        Args:
            history: Chat history handed to the provider.

        Returns:
            The summary envelope; ``{"summary": ""}`` on any provider failure.
        """
        summary = ""
        try:
            summary = await self.llm.generate(history, request_json=False)
        except Exception as e:  # legacy-parity(spec-0002): a failed summary degrades to empty
            import traceback

            # TODO(spec-0004): the stderr dump predates the project logger; retire with
            # the voice-runtime migration.
            traceback.print_exc()
            logger.error(f"error in generating summary: {e}")
        return {_SUMMARY_KEY: summary}
