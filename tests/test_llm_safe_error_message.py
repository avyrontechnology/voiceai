"""A failing LLM must never speak its exception to the caller.

Both conversational agents used to yield ``f"An error occurred: {e}"`` with ``end_of_stream=True``,
which goes straight to TTS. Provider exceptions carry request ids, org ids, endpoint hosts and key
fragments (rate-limit and auth errors especially), so the caller heard them read aloud. The turn still
needs a terminal chunk, so the fix is a fixed, localisable apology plus a logged ``error_id``.

The knowledgebase agent also used to fall back to a raw ``openai.OpenAI`` client for an unknown
provider. That object has no ``generate_stream``, so the first turn raised AttributeError inside
``generate()`` — and spoke it. An unsupported provider is a config bug: fail at construction.
"""

import logging

import pytest
from unittest.mock import MagicMock, patch

from voiceai.agent_types.graph_agent import GraphAgent
from voiceai.agent_types.knowledgebase_agent import KnowledgeBaseAgent
from voiceai.constants import (
    LLM_FAILURE_SPOKEN_MESSAGE,
    LLM_FAILURE_SPOKEN_MESSAGES,
    llm_failure_spoken_message,
)
from voiceai.errors import ConfigurationError
from voiceai.llms.types import LLMStreamChunk

# A realistic leak: an OpenAI rate-limit message naming the org, the key suffix and a request id.
SECRET_TEXT = "Rate limit reached for org-ACME on sk-proj-abc123def: request id req_9f8e7d6c5b4a"


class _Boom(Exception):
    def __init__(self):
        super().__init__(SECRET_TEXT)


def _spoken(chunks):
    """The text a caller would hear from a turn's chunks."""
    return " ".join(c.data for c in chunks if isinstance(c, LLMStreamChunk) and isinstance(c.data, str))


def _assert_safe(chunks, expected):
    terminal = [c for c in chunks if isinstance(c, LLMStreamChunk) and c.end_of_stream]
    assert len(terminal) == 1, "the turn needs exactly one terminal chunk or the task manager hangs"
    assert terminal[0].data == expected

    heard = _spoken(chunks)
    assert SECRET_TEXT not in heard
    assert "sk-proj" not in heard and "req_9f8e7d6c5b4a" not in heard and "org-ACME" not in heard
    assert "An error occurred" not in heard
    assert type(_Boom()).__name__ not in heard


# ---------------------------------------------------------------------------
# The constants
# ---------------------------------------------------------------------------


def test_english_is_the_fallback_for_unknown_languages():
    assert LLM_FAILURE_SPOKEN_MESSAGES["en"] == LLM_FAILURE_SPOKEN_MESSAGE
    assert "hi" in LLM_FAILURE_SPOKEN_MESSAGES
    for language in (None, "", "xx", "klingon"):
        assert llm_failure_spoken_message(language) == LLM_FAILURE_SPOKEN_MESSAGE


@pytest.mark.parametrize("language", ["hi", "hi-IN", "HI", "hi_IN"])
def test_region_and_case_variants_resolve_to_the_base_language(language):
    assert llm_failure_spoken_message(language) == LLM_FAILURE_SPOKEN_MESSAGES["hi"]


# ---------------------------------------------------------------------------
# GraphAgent
# ---------------------------------------------------------------------------


def _graph_agent(llm, **overrides):
    config = {
        "agent_information": "Test agent",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.7,
        "max_tokens": 150,
        "current_node_id": "start",
        "nodes": [{"id": "start", "prompt": "hi", "edges": []}],
    }
    config.update(overrides)
    with (
        patch("voiceai.agent_types.graph_agent.OpenAI", return_value=MagicMock()),
        patch("voiceai.agent_types.graph_agent.SUPPORTED_LLM_PROVIDERS", {"openai": MagicMock(return_value=llm)}),
        patch("voiceai.agent_types.graph_agent.OpenAiLLM", return_value=MagicMock()),
    ):
        return GraphAgent(config)


def _raising_llm():
    llm = MagicMock()

    def _boom(*_args, **_kwargs):
        raise _Boom()

    llm.generate_stream = MagicMock(side_effect=_boom)
    llm.trigger_function_call = False
    return llm


async def _run(agent, meta_info=None):
    return [chunk async for chunk in agent.generate([{"role": "user", "content": "hello"}], meta_info=meta_info or {})]


async def test_graph_agent_speaks_the_safe_message_not_the_exception():
    agent = _graph_agent(_raising_llm())
    _assert_safe(await _run(agent), LLM_FAILURE_SPOKEN_MESSAGE)


async def test_graph_agent_logs_the_failure_with_a_correlation_id(caplog):
    agent = _graph_agent(_raising_llm())
    with caplog.at_level(logging.ERROR, logger="voiceai.agent_types.graph_agent"):
        await _run(agent)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("error_id=" in message for message in errors)
    # The operator still needs the real cause; only the caller is shielded from it.
    assert any(SECRET_TEXT in message for message in errors)


async def test_graph_agent_uses_the_detected_language():
    agent = _graph_agent(_raising_llm(), language="hi")
    _assert_safe(await _run(agent, {"detected_language": "hi"}), LLM_FAILURE_SPOKEN_MESSAGES["hi"])


async def test_graph_agent_falls_back_to_the_configured_language():
    agent = _graph_agent(_raising_llm(), language="hi")
    _assert_safe(await _run(agent), LLM_FAILURE_SPOKEN_MESSAGES["hi"])


# ---------------------------------------------------------------------------
# KnowledgeBaseAgent
# ---------------------------------------------------------------------------


def _kb_config(**overrides):
    config = {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.7,
        "max_tokens": 150,
        "prompt": "You are a helpful agent.",
    }
    config.update(overrides)
    return config


def _kb_agent(llm, **overrides):
    with (
        patch("voiceai.agent_types.knowledgebase_agent.OpenAiLLM", return_value=MagicMock()),
        patch(
            "voiceai.agent_types.knowledgebase_agent.SUPPORTED_LLM_PROVIDERS",
            {"openai": MagicMock(return_value=llm)},
        ),
    ):
        return KnowledgeBaseAgent(_kb_config(**overrides))


async def test_knowledgebase_agent_speaks_the_safe_message_not_the_exception():
    agent = _kb_agent(_raising_llm())
    _assert_safe(await _run(agent), LLM_FAILURE_SPOKEN_MESSAGE)


async def test_knowledgebase_agent_logs_the_failure_with_a_correlation_id(caplog):
    agent = _kb_agent(_raising_llm())
    with caplog.at_level(logging.ERROR, logger="voiceai.agent_types.knowledgebase_agent"):
        await _run(agent)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("error_id=" in message for message in errors)
    assert any(SECRET_TEXT in message for message in errors)


async def test_knowledgebase_agent_uses_the_detected_language():
    agent = _kb_agent(_raising_llm())
    _assert_safe(await _run(agent, {"detected_language": "hi"}), LLM_FAILURE_SPOKEN_MESSAGES["hi"])


def test_unknown_provider_raises_a_configuration_error():
    with (
        patch("voiceai.agent_types.knowledgebase_agent.OpenAiLLM", return_value=MagicMock()),
        patch("voiceai.agent_types.knowledgebase_agent.SUPPORTED_LLM_PROVIDERS", {"openai": MagicMock()}),
        pytest.raises(ConfigurationError) as excinfo,
    ):
        KnowledgeBaseAgent(_kb_config(provider="totally-not-a-provider"))

    error = excinfo.value
    assert "totally-not-a-provider" in str(error)
    assert error.path == "tools_config.llm_agent.llm_config.provider"
    assert error.http_status == 400


def test_a_provider_that_fails_to_construct_is_classified_not_swallowed():
    """The old fallback returned an openai.OpenAI client, which has no generate_stream."""
    failing = MagicMock(side_effect=RuntimeError("no api key"))
    with (
        patch("voiceai.agent_types.knowledgebase_agent.OpenAiLLM", return_value=MagicMock()),
        patch("voiceai.agent_types.knowledgebase_agent.SUPPORTED_LLM_PROVIDERS", {"openai": failing}),
        pytest.raises(Exception) as excinfo,
    ):
        KnowledgeBaseAgent(_kb_config())

    assert not hasattr(excinfo.value, "chat")  # not an openai client handed back as an "LLM"
    assert "no api key" in str(excinfo.value)
