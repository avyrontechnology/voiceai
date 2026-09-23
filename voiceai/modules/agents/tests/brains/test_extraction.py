"""Characterization: `ExtractionContextualAgent` (spec 0002, step A6).

Pins the legacy surface before the move: the ignored `prompt` argument, the bookkeeping
attributes, and the JSON-forced single-shot `generate`.
"""

from __future__ import annotations

from voiceai.agent_types.extraction_agent import ExtractionContextualAgent


class FakeExtractionLLM:
    """Duck-typed LLM answering a canned payload from `generate`."""

    def __init__(self, result=None):
        self.result = {"extracted": True} if result is None else result
        self.calls = []

    async def generate(self, history, **kwargs):
        self.calls.append((history, kwargs))
        return self.result


def test_initial_state_and_ignored_prompt():
    """`prompt` is accepted and dropped (legacy parity); bookkeeping starts zeroed."""
    llm = FakeExtractionLLM()
    agent = ExtractionContextualAgent(llm, prompt="ignored system prompt")

    assert agent.llm is llm
    assert agent.current_messages == 0
    assert agent.is_inference_on is False
    assert agent.has_intro_been_sent is False
    assert "prompt" not in vars(agent)


async def test_generate_forces_json_and_returns_the_llm_answer():
    llm = FakeExtractionLLM(result={"name": "Ada"})
    agent = ExtractionContextualAgent(llm)
    history = [{"role": "user", "content": "my name is Ada"}]

    assert await agent.generate(history) == {"name": "Ada"}
    assert llm.calls == [(history, {"request_json": True})]
