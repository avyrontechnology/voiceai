"""Shared builders for the graph-brain parity suites (spec 0002, step A7).

Behavior parity is proven primarily by the legacy pinning suites under tests/ (rewired
to the new patch namespaces in the A7 commit); these arch suites exercise the SAME
brain through the NEW import path so the coverage gate measures the split package.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from voiceai.modules.agents.brains.graph import GraphAgent

GENERATION = "voiceai.modules.agents.brains.graph.generation"
ROUTING = "voiceai.modules.agents.brains.graph.routing"
RAG = "voiceai.modules.agents.brains.graph.rag"


async def _async_iter(items):
    for item in items:
        yield item


def expr(variable, operator, value=None):
    """One expression-edge condition in the legacy wire shape."""
    cond = {"variable": variable, "operator": operator}
    if value is not None:
        cond["value"] = value
    return {"logic": "and", "conditions": [cond]}


def base_config(nodes, current_node_id, **overrides):
    """The minimal legacy agent config the engine seam passes."""
    cfg = {
        "agent_information": "Test agent",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.7,
        "max_tokens": 150,
        "current_node_id": current_node_id,
        "nodes": nodes,
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture()
def make_agent():
    """Factory building a real GraphAgent with the generation lookups patched offline."""

    def _make(config, llm_factory=None):
        mock_llm = MagicMock()
        mock_llm.generate_stream = MagicMock(side_effect=lambda *a, **k: _async_iter([]))
        mock_llm.trigger_function_call = False
        providers = llm_factory if llm_factory is not None else {"openai": MagicMock(return_value=mock_llm)}
        with (
            patch(f"{GENERATION}.OpenAI", return_value=MagicMock()),
            patch(f"{GENERATION}.SUPPORTED_LLM_PROVIDERS", providers),
            patch(f"{GENERATION}.OpenAiLLM", return_value=MagicMock()),
        ):
            agent = GraphAgent(config)
        agent._test_llm = mock_llm  # type: ignore[attr-defined] # test-only handle
        return agent

    return _make


def routing_response(function_name, function_args_json):
    """A routing-client response carrying one tool call (usage absent)."""
    tool_call = MagicMock()
    tool_call.function.name = function_name
    tool_call.function.arguments = function_args_json
    message = MagicMock()
    message.tool_calls = [tool_call]
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    return response
