"""The single-shot extraction brain (spec 0002, A6).

Behavior-preserving verbatim move of `voiceai/agent_types/extraction_agent.py`: one
JSON-forced `generate` over the composed LLM, plus the legacy bookkeeping attributes.
"""

from __future__ import annotations

from typing import Any

from voiceai.common.logger import get_logger
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.constants import MODULE_NAME

__all__ = ["ExtractionContextualAgent"]

logger = get_logger(MODULE_NAME)


class ExtractionContextualAgent(BaseAgent):
    """Runs the extraction task's LLM once, forcing a JSON answer.

    Args:
        llm: The extraction LLM (duck-typed legacy provider: `generate`).
        prompt: Accepted and dropped (# legacy-parity(spec-0002): the legacy constructor
            never stored it; the engine passes the system prompt through the history).
    """

    def __init__(self, llm: Any, prompt: str | None = None) -> None:  # why: duck-typed provider seam
        super().__init__()
        self.llm = llm
        self.current_messages = 0
        self.is_inference_on = False
        self.has_intro_been_sent = False

    async def generate(self, history: list[dict[str, Any]]) -> Any:  # why: the provider answers raw JSON text
        """Answer the LLM's JSON-forced completion for the given history.

        Args:
            history: Chat history handed to the provider.

        Returns:
            The provider's answer, untouched.
        """
        json_data = await self.llm.generate(history, request_json=True)
        return json_data
