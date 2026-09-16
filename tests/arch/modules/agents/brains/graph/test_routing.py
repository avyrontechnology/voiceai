"""Parity suite for the graph brain's routing collaborator (spec 0002, step A7).

Client selection (Groq/Azure/OpenAI), the overflow hop, the single intent-LLM call and
the three-tier decide precedence, exercised through the NEW import path.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError

from voiceai.modules.agents.brains.graph.routing import _DETERMINISTIC_REASONING_PREFIX

from .conftest import ROUTING, base_config, expr, routing_response


def _plain_nodes():
    return [
        {
            "id": "greeting",
            "prompt": "Greet.",
            "edges": [
                {
                    "to_node_id": "booking",
                    "condition": "user wants to book",
                    "function_name": "go_to_booking",
                    "parameters": {"appointment_type": "string"},
                },
            ],
        },
        {"id": "booking", "prompt": "Book.", "edges": []},
    ]


# ---------------------------------------------------------------------------
# init_routing_client
# ---------------------------------------------------------------------------


class TestInitRoutingClient:
    def test_openai_default_model_and_client(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        assert agent.routing_provider == "openai"
        assert agent.routing_model == "gpt-4.1-mini"
        assert agent.routing_client is agent.openai

    def test_groq_requested_but_unavailable_falls_back_to_openai(self, make_agent):
        env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            agent = make_agent(base_config(_plain_nodes(), "greeting", routing_provider="groq"))
        assert agent.routing_provider == "openai"
        assert agent.routing_client is agent.openai

    def test_groq_available_builds_groq_client(self, make_agent):
        groq_cls = MagicMock()
        with (
            patch(f"{ROUTING}.GROQ_AVAILABLE", True),
            patch(f"{ROUTING}.Groq", groq_cls),
            patch.dict(os.environ, {"GROQ_API_KEY": "gk-test"}),
        ):
            agent = make_agent(base_config(_plain_nodes(), "greeting", routing_provider="groq"))
        groq_cls.assert_called_once_with(api_key="gk-test")
        assert agent.routing_model == "llama-3.3-70b-versatile"

    def test_route_routing_to_conversation_adopts_the_conversation_model(self, make_agent):
        agent = make_agent(
            base_config(_plain_nodes(), "greeting", model="azure/gpt-4o", route_routing_to_conversation=True)
        )
        assert agent.routing_model == "gpt-4o"

    def test_azure_client_with_overflow_config(self, make_agent):
        azure_cls = MagicMock()
        overflow = {"api_key": "ok", "base_url": "https://of.example.com", "model": "gpt-4o-mini"}
        with patch(f"{ROUTING}.AzureOpenAI", azure_cls):
            agent = make_agent(
                base_config(
                    _plain_nodes(),
                    "greeting",
                    routing_provider="azure",
                    routing_model="azure/gpt-4o",
                    llm_key="ak",
                    base_url="https://az.example.com",
                    api_version="2024-12-01-preview",
                    overflow_llm=overflow,
                )
            )
        assert agent._routing_overflow_cfg == overflow
        assert agent.routing_model == "gpt-4o"
        # An overflow config disables client retries so saturation surfaces immediately.
        assert azure_cls.call_args.kwargs["max_retries"] == 0

    def test_azure_without_model_takes_the_env_default(self, make_agent):
        with patch(f"{ROUTING}.AzureOpenAI", MagicMock()):
            agent = make_agent(
                base_config(_plain_nodes(), "greeting", routing_provider="azure", base_url="https://az.example.com")
            )
        assert agent._routing_overflow_cfg is None
        assert agent.routing_model == "gpt-4.1-mini"


# ---------------------------------------------------------------------------
# routing_create (the overflow hop)
# ---------------------------------------------------------------------------


class TestRoutingCreate:
    def test_success_answers_response_and_no_overflow(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        sentinel = MagicMock()
        agent.routing_client = MagicMock()
        agent.routing_client.chat.completions.create = MagicMock(return_value=sentinel)
        assert agent._routing_create({"model": "m"}) == (sentinel, False)

    def test_saturation_overflows_lazily_with_priority_tier(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        agent._routing_overflow_cfg = {"api_key": "ok", "base_url": "https://of.example.com", "model": "big"}
        agent._routing_overflow_client = None
        agent.routing_client = MagicMock()
        agent.routing_client.chat.completions.create = MagicMock(
            side_effect=APIConnectionError(request=httpx.Request("POST", "https://x.invalid"))
        )
        sentinel = MagicMock()
        overflow_client = MagicMock()
        overflow_client.chat.completions.create = MagicMock(return_value=sentinel)
        with (
            patch(f"{ROUTING}.should_overflow", return_value=True),
            patch(f"{ROUTING}.OpenAI", return_value=overflow_client) as openai_cls,
            patch(f"{ROUTING}.get_shared_sync_http_client", return_value=MagicMock()),
        ):
            result = agent._routing_create({"model": "m"})
        assert result == (sentinel, True)
        assert openai_cls.call_args.kwargs["base_url"] == "https://of.example.com"
        overflow_kwargs = overflow_client.chat.completions.create.call_args.kwargs
        assert overflow_kwargs["model"] == "big"
        assert overflow_kwargs["service_tier"] == "priority"

    def test_without_overflow_config_the_error_re_raises(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        agent.routing_client = MagicMock()
        agent.routing_client.chat.completions.create = MagicMock(
            side_effect=APIConnectionError(request=httpx.Request("POST", "https://x.invalid"))
        )
        with pytest.raises(APIConnectionError):
            agent._routing_create({"model": "m"})


# ---------------------------------------------------------------------------
# decide_next_node_llm
# ---------------------------------------------------------------------------


def _capture_routing(agent, response=None):
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        if response is None:
            resp = MagicMock()
            resp.usage = None
            resp.choices[0].message.tool_calls = None
            return resp
        return response

    agent.routing_client = MagicMock()
    agent.routing_client.chat.completions.create = _create
    return captured


class TestDecideNextNodeLlm:
    async def test_transition_pops_reasoning_and_confidence(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        response = routing_response("go_to_booking", '{"appointment_type": "x", "reasoning": "r", "confidence": 0.9}')
        _capture_routing(agent, response)
        node = agent.get_node_by_id("greeting")
        result = await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "book"}], 0.0)
        next_node, params, _, messages, tools, reasoning, confidence, _ = result
        assert next_node == "booking"
        assert params == {"appointment_type": "x"}
        assert reasoning == "r" and confidence == 0.9
        assert messages[0]["role"] == "system" and "Routing Guidelines" in messages[0]["content"]
        assert any(t["function"]["name"] == "go_to_booking" for t in tools)

    async def test_default_edge_is_offered_and_stay_is_dropped(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        response = routing_response("transition_to_general", '{"reasoning": "d", "confidence": 0.5}')
        _capture_routing(agent, response)
        node = agent.get_node_by_id("greeting")
        default = {"to_node_id": "general", "condition_type": "unconditional"}
        result = await agent._decide_next_node_llm(node, node["edges"], [], 0.0, default_edge=default)
        assert result[0] == "general"
        names = [t["function"]["name"] for t in result[4]]
        assert "stay_on_current_node" not in names and "transition_to_general" in names

    async def test_stay_on_current_node_answers_no_decision(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        response = routing_response("stay_on_current_node", '{"reasoning": "unclear", "confidence": 0.2}')
        _capture_routing(agent, response)
        node = agent.get_node_by_id("greeting")
        result = await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "hm"}], 0.0)
        assert result[0] is None and result[5] == "unclear"

    async def test_unknown_function_and_no_tool_call_answer_no_decision(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        node = agent.get_node_by_id("greeting")
        response = routing_response("not_an_edge", '{"reasoning": "x", "confidence": 0.1}')
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.completion_tokens_details = None
        usage.prompt_tokens_details = None
        response.usage = usage
        _capture_routing(agent, response)
        result = await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "?"}], 0.0)
        assert result[0] is None
        assert result[7]["input_tokens"] == 10 and result[7]["overflowed"] is False

        _capture_routing(agent)  # no tool_calls at all
        result = await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "?"}], 0.0)
        assert result[0] is None and result[5] is None

    async def test_gpt5_models_send_effort_and_completion_tokens(self, make_agent):
        agent = make_agent(
            base_config(_plain_nodes(), "greeting", routing_model="gpt-5.4-mini", service_tier="priority")
        )
        captured = _capture_routing(agent)
        node = agent.get_node_by_id("greeting")
        await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "hi"}], 0.0)
        assert captured["max_completion_tokens"] == 150
        assert "temperature" not in captured
        assert captured["service_tier"] == "priority"
        assert agent._routing_reasoning_effort_used == captured["reasoning_effort"]

    async def test_non_gpt5_models_send_temperature_zero(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        captured = _capture_routing(agent)
        node = agent.get_node_by_id("greeting")
        await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "hi"}], 0.0)
        assert captured["max_tokens"] == 250 and captured["temperature"] == 0.0

    async def test_create_failure_answers_no_decision_with_spent_payloads(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        agent.routing_client = MagicMock()
        agent.routing_client.chat.completions.create = MagicMock(side_effect=RuntimeError("boom"))
        node = agent.get_node_by_id("greeting")
        result = await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "hi"}], 0.0)
        assert result[0] is None and result[3] is not None and result[4] is not None

    async def test_tool_call_history_is_replayed_into_the_routing_prompt(self, make_agent):
        agent = make_agent(
            base_config(
                _plain_nodes(),
                "greeting",
                routing_instructions="Route {name} carefully.",
                context_data={"name": "Rahul", "recipient_data": {"name": "Rahul"}},
            )
        )
        captured = _capture_routing(agent)
        node = agent.get_node_by_id("greeting")
        history = [
            {"role": "user", "content": "book it"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        await agent._decide_next_node_llm(node, node["edges"], history, 0.0)
        roles = [m["role"] for m in captured["messages"]]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]
        assert "Rahul" in captured["messages"][0]["content"]

    async def test_empty_node_history_falls_back_to_the_latest_user_message(self, make_agent):
        agent = make_agent(base_config(_plain_nodes(), "greeting"))
        captured = _capture_routing(agent)
        agent.current_node_entry_index = 1
        node = agent.get_node_by_id("greeting")
        await agent._decide_next_node_llm(node, node["edges"], [{"role": "user", "content": "only"}], 0.0)
        assert captured["messages"][-1] == {"role": "user", "content": "only"}


# ---------------------------------------------------------------------------
# decide_next_node_with_functions
# ---------------------------------------------------------------------------


class TestDecideNextNodeWithFunctions:
    def _tiered_nodes(self):
        return [
            {
                "id": "greeting",
                "prompt": "Greet.",
                "edges": [
                    {
                        "to_node_id": "hindi",
                        "condition_type": "expression",
                        "expression": expr("detected_language", "eq", "hi"),
                    },
                    {"to_node_id": "booking", "condition": "wants booking", "function_name": "go_to_booking"},
                    {"to_node_id": "fallback", "condition_type": "unconditional"},
                ],
            },
            {"id": "hindi", "prompt": "H.", "edges": []},
            {"id": "booking", "prompt": "B.", "edges": []},
            {"id": "fallback", "prompt": "F.", "edges": []},
        ]

    async def test_expression_tier_skips_the_llm(self, make_agent):
        agent = make_agent(base_config(self._tiered_nodes(), "greeting", context_data={"detected_language": "hi"}))
        with patch.object(agent, "_decide_next_node_llm", new_callable=AsyncMock) as decide:
            result = await agent.decide_next_node_with_functions([{"role": "user", "content": "namaste"}])
        decide.assert_not_called()
        assert result[0] == "hindi" and result[6] == 1.0
        assert result[5].startswith(_DETERMINISTIC_REASONING_PREFIX)

    async def test_intent_no_decision_falls_to_catch_all_with_spent_telemetry(self, make_agent):
        agent = make_agent(base_config(self._tiered_nodes(), "greeting", context_data={"detected_language": "en"}))
        no_decision: tuple = (
            None,
            None,
            12.0,
            [{"role": "system", "content": "r"}],
            [],
            None,
            None,
            {"input_tokens": 1},
        )
        with patch.object(agent, "_decide_next_node_llm", new_callable=AsyncMock, return_value=no_decision):
            result = await agent.decide_next_node_with_functions([{"role": "user", "content": "hm"}])
        assert result[0] == "fallback"
        assert result[3] is not None and result[7] == {"input_tokens": 1}
        assert "intent: no match" in agent._last_deterministic_eval

    async def test_tier_three_takes_the_default_without_intent_edges(self, make_agent):
        nodes = [
            {"id": "a", "prompt": "A.", "edges": [{"to_node_id": "b", "condition_type": "unconditional"}]},
            {"id": "b", "prompt": "B.", "edges": []},
        ]
        agent = make_agent(base_config(nodes, "a"))
        result = await agent.decide_next_node_with_functions([{"role": "user", "content": "x"}])
        assert result[0] == "b" and result[6] == 1.0

    async def test_no_edges_and_missing_node_both_stay(self, make_agent):
        agent = make_agent(base_config([{"id": "leaf", "prompt": "L.", "edges": []}], "leaf"))
        assert (await agent.decide_next_node_with_functions([]))[0] is None
        agent.current_node_id = "ghost"
        assert (await agent.decide_next_node_with_functions([]))[0] is None
