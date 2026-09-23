"""Parity suite for the graph brain's traversal collaborator (spec 0002, step A7).

Transition tools, edge classification, events, context enrichment, the router chain
and the hold machinery, exercised through the NEW import path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from voiceai.llms.types import LLMStreamChunk
from voiceai.modules.agents.brains.graph import GraphAgent
from voiceai.modules.agents.brains.graph.routing import _DETERMINISTIC_REASONING_PREFIX

from .conftest import base_config, expr


def _leaf(node_id):
    return {"id": node_id, "prompt": f"{node_id}.", "edges": []}


# ---------------------------------------------------------------------------
# Transition tools
# ---------------------------------------------------------------------------


class TestTransitionTools:
    def test_edge_function_name_explicit_and_generated(self):
        assert GraphAgent._edge_function_name({"function_name": "go", "to_node_id": "x"}) == "go"
        assert GraphAgent._edge_function_name({"to_node_id": "x"}) == "transition_to_x"

    def test_tools_carry_parameters_reasoning_and_confidence(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        edges = [
            {"to_node_id": "b", "condition": "wants b", "parameters": {"city": "string"}},
        ]
        tools = agent._build_transition_tools_for_edges(edges)
        spec = tools[0]["function"]
        assert spec["name"] == "transition_to_b"
        assert set(spec["parameters"]["required"]) == {"city", "reasoning", "confidence"}
        assert tools[-1]["function"]["name"] == "stay_on_current_node"

    def test_function_description_is_context_substituted(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a", context_data={"recipient_data": {"name": "Rahul"}}))
        edges = [{"to_node_id": "b", "function_description": "Send {name} on."}]
        tools = agent._build_transition_tools_for_edges(edges, allow_stay=False)
        assert tools[0]["function"]["description"] == "Send Rahul on."

    def test_build_transition_tools_caches_per_node_and_evicts(self, make_agent):
        agent = make_agent(base_config([_leaf("a"), _leaf("b")], "a"))
        agent._transition_tools_cache_max_size = 1
        node_a = {"id": "a", "edges": [{"to_node_id": "b", "condition": "x"}]}
        first = agent._build_transition_tools(node_a)
        assert agent._build_transition_tools(node_a) is first  # cache hit
        agent._build_transition_tools({"id": "b", "edges": []})  # evicts "a"
        assert "a" not in agent._transition_tools_cache

    def test_get_edge_by_function_name_on_the_node(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        node = {"id": "a", "edges": [{"to_node_id": "b", "function_name": "go_b"}]}
        assert agent._get_edge_by_function_name(node, "go_b")["to_node_id"] == "b"
        assert agent._get_edge_by_function_name(node, "nope") is None


# ---------------------------------------------------------------------------
# Classification and deterministic evaluation
# ---------------------------------------------------------------------------


class TestClassification:
    def test_event_edges_are_excluded_and_priorities_sort(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        edges = [
            {"to_node_id": "e", "condition_type": "event", "event_name": "ping"},
            {"to_node_id": "low", "condition_type": "expression", "priority": 9, "expression": expr("x", "eq", "1")},
            {"to_node_id": "high", "condition_type": "expression", "priority": 1, "expression": expr("x", "eq", "1")},
            {"to_node_id": "intent", "condition": "wants it", "priority": None},
        ]
        det, llm = agent._classify_edges(edges)
        assert [e["to_node_id"] for e in det] == ["high", "low"]
        assert [e["to_node_id"] for e in llm] == ["intent"]

    def test_evaluate_deterministic_edges_traces_and_matches(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a", context_data={"x": "1"}))
        edges = [
            {"to_node_id": "no", "condition_type": "expression", "expression": expr("x", "eq", "2")},
            {"to_node_id": "yes", "condition_type": "expression", "expression": expr("x", "eq", "1")},
        ]
        matched, evaluations = agent._evaluate_deterministic_edges(edges)
        assert matched["to_node_id"] == "yes"
        assert len(evaluations) == 2 and "matched=False" in evaluations[0]

    def test_match_expression_edge_reports_no_match(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        node = {"id": "a", "edges": [{"to_node_id": "b", "condition_type": "unconditional"}]}
        matched, trace = agent._match_expression_edge(node)
        assert matched is None and trace == "no expression edge matched"

    def test_catch_all_edge_and_reasoning(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        node = {
            "id": "a",
            "edges": [
                {"to_node_id": "g", "condition_type": "unconditional", "priority": 5},
                {"to_node_id": "p", "condition_type": "unconditional", "priority": 1},
            ],
        }
        edge = agent._catch_all_edge(node)
        assert edge["to_node_id"] == "p"
        assert agent._catch_all_reasoning(edge).startswith(f"{_DETERMINISTIC_REASONING_PREFIX}unconditional:")
        assert agent._catch_all_edge({"id": "x", "edges": []}) is None


# ---------------------------------------------------------------------------
# process_event
# ---------------------------------------------------------------------------


class TestProcessEvent:
    def _nodes(self):
        return [
            {
                "id": "waiting",
                "prompt": "W.",
                "edges": [{"to_node_id": "next", "condition_type": "event", "event_name": "link_opened"}],
            },
            _leaf("next"),
        ]

    def test_matching_event_transitions_and_resets_node_state(self, make_agent):
        agent = make_agent(base_config(self._nodes(), "waiting"))
        result = agent.process_event({"event": "link_opened", "properties": {"link": "https://x.example"}})
        assert result["matched"] is True and result["new_node_id"] == "next"
        assert agent.current_node_id == "next" and agent.node_history[-1] == "next"
        assert agent.context_data["link"] == "https://x.example"
        assert agent._active_node_first_response_delivered is False

    def test_unmatched_event_updates_context_silently(self, make_agent):
        agent = make_agent(base_config(self._nodes(), "waiting"))
        result = agent.process_event({"event": "unknown", "properties": {"k": "v"}})
        assert result == {"matched": False, "event": "unknown"}
        assert agent.current_node_id == "waiting" and agent.context_data["k"] == "v"

    def test_missing_current_node_answers_unmatched(self, make_agent):
        agent = make_agent(base_config(self._nodes(), "waiting"))
        agent.current_node_id = "ghost"
        assert agent.process_event({"event": "link_opened"})["matched"] is False


# ---------------------------------------------------------------------------
# Context enrichment and turn counts
# ---------------------------------------------------------------------------


class TestRoutingContext:
    def test_turn_counts_respect_the_node_entry_index(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        history = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "r"},
            {"role": "user", "content": "2"},
        ]
        agent.current_node_entry_index = 2
        assert agent._compute_turn_counts(history) == (1, 2)

    def test_enrich_routing_context_writes_counters_and_time_vars(self, make_agent):
        agent = make_agent(
            base_config([_leaf("a")], "a", context_data={"recipient_data": {"timezone": "Asia/Kolkata"}})
        )
        agent._silence_repeats = 2
        agent._enrich_routing_context([{"role": "user", "content": "hi"}])
        assert agent.context_data["_total_turns"] == 1
        assert agent.context_data["_node_turns"] == 1
        assert agent.context_data["_silence_repeats"] == 2
        assert "current_date" in agent.context_data["recipient_data"]


# ---------------------------------------------------------------------------
# Router chain, hold and the terminal chunk
# ---------------------------------------------------------------------------


class TestRouterChain:
    async def test_intent_hop_records_llm_telemetry(self, make_agent):
        nodes = [
            {
                "id": "r",
                "node_type": "router",
                "edges": [
                    {"to_node_id": "billing", "condition": "billing question"},
                    {"to_node_id": "general", "condition_type": "unconditional"},
                ],
            },
            _leaf("billing"),
            _leaf("general"),
        ]
        agent = make_agent(base_config(nodes, "r"))
        pick: tuple = (
            "billing",
            {"topic": "invoice"},
            9.0,
            [{"role": "system", "content": "r"}],
            [],
            "asks",
            0.9,
            None,
        )
        with patch.object(agent, "_decide_next_node_llm", new_callable=AsyncMock, return_value=pick):
            hops = await agent._resolve_router_chain([{"role": "user", "content": "invoice"}])
        assert agent.current_node_id == "billing" and agent.context_data["topic"] == "invoice"
        assert hops[0]["routing_type"] == "llm" and hops[0]["routing_model"] is not None

    async def test_runtime_cycle_is_bounded_by_the_visited_set(self, make_agent):
        nodes = [
            {"id": "r1", "node_type": "router", "edges": [{"to_node_id": "r2", "condition_type": "unconditional"}]},
            {"id": "r2", "node_type": "router", "edges": [{"to_node_id": "r1", "condition_type": "unconditional"}]},
        ]
        agent = make_agent(base_config(nodes, "r1"))
        hops = await agent._resolve_router_chain([])
        assert len(hops) == 2  # r1->r2, r2->r1, then r1 already visited -> stop

    async def test_router_without_any_edge_breaks_the_chain(self, make_agent):
        nodes = [{"id": "r", "node_type": "router", "edges": []}]
        agent = make_agent(base_config(nodes, "r"))
        assert await agent._resolve_router_chain([]) == []
        assert agent.current_node_id == "r"

    def test_hold_routing_info_shape(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        info = agent._hold_routing_info(True)
        assert info["routing_type"] == "hold" and info["transitioned"] is False
        assert info["reasoning"] == f"{_DETERMINISTIC_REASONING_PREFIX}hold:first_response_undelivered"
        assert info["is_silence_trigger"] is True

    def test_should_hold_only_for_undelivered_llm_nodes(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        node = agent.get_node_by_id("a")
        assert agent._should_hold_for_first_delivery(node) is False  # initial node counts as delivered
        agent._advance_to_node("a", 0)
        assert agent._should_hold_for_first_delivery(node) is True
        agent.mark_first_response_delivered()
        assert agent._should_hold_for_first_delivery(node) is False

    def test_end_turn_chunk_is_a_terminal_empty_stream_chunk(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        chunk = agent._end_turn_chunk({"sequence_id": 7}, 0.0)
        assert isinstance(chunk, LLMStreamChunk) and chunk.end_of_stream and chunk.data == ""
        assert chunk.latency is not None and chunk.latency.sequence_id == 7

    def test_router_hop_info_attributes_model_only_on_llm_calls(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        silent = agent._router_hop_info(
            "p", routing_type="deterministic", latency_ms=1.0, reasoning="r", confidence=1.0
        )
        assert silent["routing_model"] is None and silent["routing_provider"] is None
        spoken = agent._router_hop_info(
            "p",
            routing_type="deterministic",
            latency_ms=1.0,
            reasoning="r",
            confidence=1.0,
            routing_messages=[{"role": "system", "content": "r"}],
        )
        assert spoken["routing_model"] == agent.routing_model
