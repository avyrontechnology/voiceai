"""Characterization: `SummarizationContextualAgent` (spec 0002, step A6).

Pins the legacy surface before the move: the ignored `prompt` argument, the plain-text
summary call, and the swallow-to-empty-summary failure path.
"""

from __future__ import annotations

from voiceai.agent_types.summarization_agent import SummarizationContextualAgent


class FakeSummaryLLM:
    """Duck-typed LLM answering a canned summary, or raising."""

    def __init__(self, summary="a short summary", error=None):
        self.summary = summary
        self.error = error
        self.calls = []

    async def generate(self, history, **kwargs):
        self.calls.append((history, kwargs))
        if self.error is not None:
            raise self.error
        return self.summary


def test_initial_state_and_ignored_prompt():
    llm = FakeSummaryLLM()
    agent = SummarizationContextualAgent(llm, prompt="ignored")

    assert agent.llm is llm
    assert agent.current_messages == 0
    assert agent.is_inference_on is False
    assert agent.has_intro_been_sent is False
    assert "prompt" not in vars(agent)


async def test_generate_wraps_the_plain_text_answer():
    llm = FakeSummaryLLM(summary="caller asked about pricing")
    agent = SummarizationContextualAgent(llm)
    history = [{"role": "user", "content": "pricing?"}]

    assert await agent.generate(history) == {"summary": "caller asked about pricing"}
    assert llm.calls == [(history, {"request_json": False})]


async def test_generate_swallows_failures_to_an_empty_summary():
    """An LLM failure degrades to `{"summary": ""}` without raising (legacy parity)."""
    agent = SummarizationContextualAgent(FakeSummaryLLM(error=RuntimeError("llm down")))

    assert await agent.generate([{"role": "user", "content": "hi"}]) == {"summary": ""}
