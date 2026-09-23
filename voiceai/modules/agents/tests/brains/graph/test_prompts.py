"""Parity suite for the graph brain's prompts collaborator (spec 0002, step A7).

Language-directive prompt assembly, forced-function tool choice, tool scoping, the
frozen-time prompt context, message building and the static playback chunk, exercised
through the NEW import path.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from voiceai.modules.agents.brains.graph import GraphAgent

from .conftest import base_config

BUILD = GraphAgent._get_prompt_with_example


def _leaf(node_id, **extra):
    return {"id": node_id, "prompt": f"{node_id}.", "edges": [], **extra}


def _tool(name):
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}},
    }


class _StubLLM:
    def __init__(self, tools, api_params, trigger=True):
        self.trigger_function_call = trigger
        self.tools = tools
        self.api_params = api_params


# ---------------------------------------------------------------------------
# get_prompt_with_example (unbound: a legacy pin calls it with a dummy self)
# ---------------------------------------------------------------------------


class TestPromptWithExample:
    def test_directive_appears_once_language_is_known(self):
        out = BUILD(MagicMock(), {"prompt": "P", "examples": None}, "en")
        assert out.startswith("P") and "LANGUAGE GUIDELINES" in out and "English" in out

    def test_matching_example_is_attached(self):
        out = BUILD(MagicMock(), {"prompt": "P", "examples": {"te": "నమస్తే"}}, "te")
        assert 'Example response: "నమస్తే"' in out

    def test_no_language_lists_all_examples(self):
        out = BUILD(MagicMock(), {"prompt": "P", "examples": {"hi": "नमस्ते", "en": "Hello"}}, None)
        assert "LANGUAGE GUIDELINES" not in out
        assert 'HI: "नमस्ते"' in out and 'EN: "Hello"' in out

    def test_no_language_no_examples_is_the_bare_prompt(self):
        assert BUILD(MagicMock(), {"prompt": "P"}, None) == "P"


# ---------------------------------------------------------------------------
# Forced tool choice + node scoping
# ---------------------------------------------------------------------------


class TestToolChoice:
    def _agent(self, make_agent, **node_extra):
        agent = make_agent(base_config([_leaf("x", **node_extra)], "x"))
        return agent

    def test_forces_the_node_function(self, make_agent):
        agent = self._agent(make_agent, function_call="end_call")
        agent.llm = _StubLLM([_tool("end_call")], {"end_call": {}})
        assert agent._get_tool_choice_for_node(history=[]) == {"type": "function", "function": {"name": "end_call"}}

    def test_no_force_without_trigger_function_call_or_function(self, make_agent):
        agent = self._agent(make_agent, function_call="end_call")
        agent.llm = _StubLLM([_tool("end_call")], {"end_call": {}}, trigger=False)
        assert agent._get_tool_choice_for_node(history=[]) is None
        agent.llm = _StubLLM([_tool("end_call")], {"end_call": {}})
        agent.current_node_id = "ghost"
        assert agent._get_tool_choice_for_node(history=[]) is None

    def test_force_dropped_when_required_prompt_vars_are_missing(self, make_agent):
        agent = make_agent(
            base_config(
                [{"id": "x", "prompt": "Confirm {payment_link}.", "function_call": "send", "edges": []}],
                "x",
                context_data={"recipient_data": {}},
            )
        )
        tools = [
            {
                "type": "function",
                "function": {"name": "send", "parameters": {"required": ["payment_link"], "properties": {}}},
            }
        ]
        agent.llm = _StubLLM(json.dumps(tools), {"send": {}})
        assert agent._missing_forced_function_vars(agent.get_node_by_id("x"), "send") == ["payment_link"]
        assert agent._get_tool_choice_for_node(history=[]) is None

    def test_force_dropped_after_the_tool_completed_this_visit(self, make_agent):
        agent = self._agent(make_agent, function_call="send")
        agent.llm = _StubLLM([_tool("send")], {"send": {}})
        history = [
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "send"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        assert agent._forced_function_already_called("send", history) is True
        assert agent._get_tool_choice_for_node(history=history) is None

    def test_pending_call_without_result_does_not_drop_the_force(self, make_agent):
        agent = self._agent(make_agent, function_call="send")
        agent.llm = _StubLLM([_tool("send")], {"send": {}})
        history = [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "send"}}]}]
        assert agent._forced_function_already_called("send", history) is False


class TestToolsForNode:
    def test_node_scoped_tools_filter_and_forced_stays_visible(self, make_agent):
        agent = make_agent(base_config([_leaf("x"), _leaf("y")], "x"))
        agent.llm = _StubLLM(
            [_tool("g"), _tool("s"), _tool("f")],
            {"g": {}, "s": {"scope": "node", "nodes": ["y"]}, "f": {"scope": "node", "nodes": ["y"]}},
        )
        node = agent.get_node_by_id("x")
        names = [t["function"]["name"] for t in agent._tools_for_node(node)]
        assert names == ["g"]
        names = [t["function"]["name"] for t in agent._tools_for_node(node, "f")]
        assert names == ["g", "f"]

    def test_nothing_filtered_or_no_tools_answers_none(self, make_agent):
        agent = make_agent(base_config([_leaf("x")], "x"))
        agent.llm = _StubLLM([_tool("g")], {"g": {}})
        assert agent._tools_for_node(agent.get_node_by_id("x")) is None
        agent.llm = _StubLLM([], {})
        assert agent._tools_for_node(agent.get_node_by_id("x")) is None
        agent.llm = None
        assert agent._tools_for_node(agent.get_node_by_id("x")) is None


# ---------------------------------------------------------------------------
# Frozen-time prompt context
# ---------------------------------------------------------------------------


class TestPromptContext:
    def test_without_recipient_data_the_context_passes_through(self, make_agent):
        agent = make_agent(base_config([_leaf("x")], "x", context_data={"k": "v"}))
        assert agent._prompt_context() == {"k": "v"}

    def test_time_vars_freeze_on_first_use(self, make_agent):
        agent = make_agent(
            base_config([_leaf("x")], "x", context_data={"recipient_data": {"timezone": "Asia/Kolkata"}})
        )
        first = agent._prompt_context()
        frozen = dict(agent._frozen_time_vars)
        assert "current_date" in frozen
        # Live enrichment may advance the clock; the prompt context stays frozen.
        agent.context_data["recipient_data"]["current_time"] = "not-a-time"
        second = agent._prompt_context()
        assert second["recipient_data"]["current_time"] == frozen["current_time"]
        assert first["recipient_data"]["current_date"] == frozen["current_date"]

    def test_recipient_without_timezone_freezes_nothing(self, make_agent):
        agent = make_agent(base_config([_leaf("x")], "x", context_data={"recipient_data": {"name": "R"}}))
        assert agent._prompt_context() is agent.context_data
        assert agent._frozen_time_vars == {}


# ---------------------------------------------------------------------------
# build_messages + static chunk
# ---------------------------------------------------------------------------


class TestBuildMessages:
    async def test_system_prompt_carries_agent_info_and_history_label(self, make_agent):
        agent = make_agent(
            base_config(
                [{"id": "x", "prompt": "Talk to {name}.", "edges": []}],
                "x",
                context_data={"recipient_data": {"name": "Rahul"}},
            )
        )
        history = [
            {"role": "system", "content": "engine pin"},
            {"role": "user", "content": "hi"},
        ]
        messages = await agent._build_messages(history)
        assert messages[0]["role"] == "system"
        assert "Test agent" in messages[0]["content"]
        assert "Talk to Rahul." in messages[0]["content"]
        assert messages[0]["content"].endswith("## Conversation History")
        assert [m["role"] for m in messages[1:]] == ["user"]  # history system messages stripped

    async def test_history_is_windowed_to_the_last_fifty(self, make_agent):
        agent = make_agent(base_config([_leaf("x")], "x"))
        history = [{"role": "user", "content": str(i)} for i in range(60)]
        messages = await agent._build_messages(history)
        assert len(messages) == 51  # system + the last 50
        assert messages[1]["content"] == "10"

    def test_static_message_chunk_substitutes_and_hashes(self, make_agent):
        agent = make_agent(base_config([_leaf("x")], "x", context_data={"recipient_data": {"name": "Rahul"}}))
        node = {"id": "x", "static_message": "Bye {name}!"}
        chunk = agent._static_message_chunk(node)
        assert chunk["static_message"] == "Bye Rahul!"
        assert len(chunk["static_audio_hash"]) == 32
        assert agent._static_message_chunk({"id": "x"}) is None
