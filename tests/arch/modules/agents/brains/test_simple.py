"""Characterization: `StreamingContextualAgent` (spec 0002, step A6).

Pins the legacy behavior through the legacy import path before the move: judgment-LLM
selection from the environment, the `check_for_completion` JSON-parse path with a faked
llm, the swallow-to-default fallbacks, the voicemail check, and the token passthrough of
`generate`. All fakes — the socket guard proves nothing goes live.
"""

from __future__ import annotations

import pytest

from voiceai.agent_types.contextual_conversational_agent import StreamingContextualAgent
from voiceai.helpers.utils import format_messages
from voiceai.prompts import VOICEMAIL_DETECTION_PROMPT

MESSAGES = [{"role": "user", "content": "hello there"}]
COMPLETION_PROMPT = "Decide whether to hang up."


class FakeStreamLLM:
    """Duck-typed main LLM: a `model` name and a recorded `generate_stream`."""

    def __init__(self, tokens=("t1", "t2"), model="fake-main-model"):
        self.model = model
        self.tokens = list(tokens)
        self.calls = []

    async def generate_stream(self, history, synthesize=False, meta_info=None):
        self.calls.append((history, synthesize, meta_info))
        for token in self.tokens:
            yield token


class FakeJudgeLLM:
    """Duck-typed judgment LLM answering a canned `(response, metadata)` pair."""

    def __init__(self, response="{}", metadata=None, error=None):
        self.response = response
        self.metadata = {} if metadata is None else metadata
        self.error = error
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error is not None:
            raise self.error
        return self.response, dict(self.metadata)


@pytest.fixture
def agent(monkeypatch):
    """An agent built with judgment-model env vars cleared (defaults pinned)."""
    monkeypatch.delenv("CHECK_FOR_COMPLETION_LLM", raising=False)
    monkeypatch.delenv("VOICEMAIL_DETECTION_LLM", raising=False)
    return StreamingContextualAgent(FakeStreamLLM())


class TestConstructor:
    def test_defaults_mirror_the_main_llm_and_the_pinned_voicemail_model(self, agent):
        """Completion judge inherits the main model; voicemail judge pins gpt-4.1-mini."""
        assert agent.conversation_completion_llm.model == "fake-main-model"
        assert agent.voicemail_llm.model == "gpt-4.1-mini"

    def test_env_vars_override_both_judgment_models(self, monkeypatch):
        monkeypatch.setenv("CHECK_FOR_COMPLETION_LLM", "judge-model")
        monkeypatch.setenv("VOICEMAIL_DETECTION_LLM", "vm-model")
        agent = StreamingContextualAgent(FakeStreamLLM())
        assert agent.conversation_completion_llm.model == "judge-model"
        assert agent.voicemail_llm.model == "vm-model"

    def test_initial_state(self, agent):
        assert agent.history == [{"content": ""}]
        assert agent.agent_name == "base-agent"


class TestCheckForCompletion:
    async def test_parses_the_json_answer_and_adds_latency(self, agent):
        """The judge's JSON body is parsed; its metadata gains a float `latency_ms`."""
        judge = FakeJudgeLLM(response='{"hangup": "Yes"}', metadata={"model": "judge"})
        agent.conversation_completion_llm = judge

        answer, metadata = await agent.check_for_completion(MESSAGES, COMPLETION_PROMPT, meta_info={"call": 1})

        assert answer == {"hangup": "Yes"}
        assert metadata["model"] == "judge"
        assert isinstance(metadata["latency_ms"], float)

    async def test_prompt_shape_and_llm_kwargs(self, agent):
        """System turn is the given prompt; user turn is `format_messages`; JSON forced."""
        judge = FakeJudgeLLM(response='{"hangup": "No"}')
        agent.conversation_completion_llm = judge

        await agent.check_for_completion(MESSAGES, COMPLETION_PROMPT, meta_info={"call": 2})

        prompt, kwargs = judge.calls[0]
        assert prompt == [
            {"role": "system", "content": COMPLETION_PROMPT},
            {"role": "user", "content": format_messages(MESSAGES)},
        ]
        assert kwargs == {"request_json": True, "ret_metadata": True, "meta_info": {"call": 2}}

    async def test_bad_json_swallows_to_keep_talking(self, agent):
        """A non-JSON judge answer degrades to `({"hangup": "No"}, {})` (legacy parity)."""
        agent.conversation_completion_llm = FakeJudgeLLM(response="not json at all")
        assert await agent.check_for_completion(MESSAGES, COMPLETION_PROMPT) == ({"hangup": "No"}, {})

    async def test_llm_error_swallows_to_keep_talking(self, agent):
        agent.conversation_completion_llm = FakeJudgeLLM(error=RuntimeError("judge down"))
        assert await agent.check_for_completion(MESSAGES, COMPLETION_PROMPT) == ({"hangup": "No"}, {})


class TestCheckForVoicemail:
    async def test_default_prompt_and_user_turn_shape(self, agent):
        """Without a custom prompt the packaged voicemail-detection prompt is sent."""
        judge = FakeJudgeLLM(response='{"is_voicemail": "Yes"}')
        agent.voicemail_llm = judge

        answer, metadata = await agent.check_for_voicemail("Please leave a message")

        prompt, kwargs = judge.calls[0]
        assert prompt == [
            {"role": "system", "content": VOICEMAIL_DETECTION_PROMPT},
            {"role": "user", "content": "User message: Please leave a message"},
        ]
        assert kwargs == {"request_json": True, "ret_metadata": True}
        assert answer == {"is_voicemail": "Yes"}
        assert isinstance(metadata["latency_ms"], float)

    async def test_custom_prompt_passes_through(self, agent):
        judge = FakeJudgeLLM(response='{"is_voicemail": "No"}')
        agent.voicemail_llm = judge

        await agent.check_for_voicemail("hi", voicemail_detection_prompt="CUSTOM VM PROMPT")

        assert judge.calls[0][0][0] == {"role": "system", "content": "CUSTOM VM PROMPT"}

    async def test_failure_swallows_to_not_a_voicemail(self, agent):
        agent.voicemail_llm = FakeJudgeLLM(error=RuntimeError("vm judge down"))
        assert await agent.check_for_voicemail("hi") == ({"is_voicemail": "No"}, {})


class TestGenerate:
    async def test_streams_the_main_llm_tokens_through(self, agent):
        """`generate` is a pure passthrough of `generate_stream` with its kwargs."""
        history = [{"role": "user", "content": "go"}]

        tokens = [token async for token in agent.generate(history, synthesize=True, meta_info={"seq": 3})]

        assert tokens == ["t1", "t2"]
        assert agent.llm.calls == [(history, True, {"seq": 3})]
