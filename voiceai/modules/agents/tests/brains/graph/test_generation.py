"""Parity suite for the graph brain's generation collaborator (spec 0002, step A7).

Construction (clients, aux judgment LLMs), the LLM factory, both judgments, and the
generate() turn loop, exercised through the NEW import path.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.llms.types import LLMStreamChunk
from voiceai.modules.agents.brains.graph import GraphAgent

from .conftest import GENERATION, base_config, expr


def _nodes():
    return [
        {
            "id": "greeting",
            "prompt": "Greet.",
            "edges": [{"to_node_id": "goodbye", "condition": "user says bye", "function_name": "go_bye"}],
        },
        {"id": "goodbye", "node_type": "static", "static_message": "Goodbye!", "edges": []},
    ]


async def _collect(agen):
    return [item async for item in agen]


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    def _construct(self, config, providers=None):
        mock_llm = MagicMock()
        mock_llm.trigger_function_call = False
        openai_cls = MagicMock()
        openai_llm_cls = MagicMock(return_value=MagicMock())
        with (
            patch(f"{GENERATION}.OpenAI", openai_cls),
            patch(
                f"{GENERATION}.SUPPORTED_LLM_PROVIDERS",
                providers if providers is not None else {"openai": MagicMock(return_value=mock_llm)},
            ),
            patch(f"{GENERATION}.OpenAiLLM", openai_llm_cls),
            patch(f"{GENERATION}.get_shared_sync_http_client", return_value="POOLED") as pool,
        ):
            agent = GraphAgent(config)
        return agent, openai_cls, openai_llm_cls, pool

    def test_custom_provider_base_url_uses_the_pooled_client(self):
        cfg = base_config(_nodes(), "greeting", provider="custom", base_url="https://llm.example.com", llm_key="k")
        _, openai_cls, _, pool = self._construct(cfg)
        pool.assert_called_once_with(base_url="https://llm.example.com", http2=False)
        assert openai_cls.call_args.kwargs["http_client"] == "POOLED"

    def test_non_custom_base_url_skips_the_pool(self):
        cfg = base_config(_nodes(), "greeting", base_url="https://llm.example.com", llm_key="k")
        _, openai_cls, _, pool = self._construct(cfg)
        pool.assert_not_called()
        assert "http_client" not in openai_cls.call_args.kwargs

    def test_azure_aux_falls_back_to_the_platform_key(self):
        cfg = base_config(_nodes(), "greeting", provider="azure", llm_key="agent-key")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "platform-key"}):
            _, _, openai_llm_cls, _ = self._construct(cfg, providers={"azure": MagicMock(return_value=MagicMock())})
        assert openai_llm_cls.call_args.kwargs["llm_key"] == "platform-key"

    def test_non_azure_aux_keeps_the_agent_credentials(self):
        cfg = base_config(_nodes(), "greeting", llm_key="agent-key", base_url="https://llm.example.com")
        _, _, openai_llm_cls, _ = self._construct(cfg)
        kwargs = openai_llm_cls.call_args.kwargs
        assert kwargs["llm_key"] == "agent-key" and kwargs["base_url"] == "https://llm.example.com"

    def test_execution_id_lands_in_recipient_data(self):
        cfg = base_config(_nodes(), "greeting", execution_id="ex-1", context_data={"recipient_data": {"name": "R"}})
        agent, _, _, _ = self._construct(cfg)
        assert agent.context_data["recipient_data"]["execution_id"] == "ex-1"


# ---------------------------------------------------------------------------
# initialize_llm
# ---------------------------------------------------------------------------


class TestInitializeLlm:
    def test_unknown_provider_falls_back_to_openai(self, make_agent):
        factory = MagicMock(return_value=MagicMock(trigger_function_call=False))
        agent = make_agent(base_config(_nodes(), "greeting", provider="martian"), llm_factory={"openai": factory})
        assert factory.call_args.kwargs["provider"] == "openai"
        assert agent.llm is factory.return_value

    def test_truthy_optional_keys_pass_through_and_falsy_are_dropped(self, make_agent):
        factory = MagicMock(return_value=MagicMock(trigger_function_call=False))
        agent = make_agent(
            base_config(_nodes(), "greeting", verbosity="high", buffer_size=0), llm_factory={"openai": factory}
        )
        kwargs = factory.call_args.kwargs
        assert kwargs["verbosity"] == "high"
        assert "buffer_size" not in kwargs  # legacy-parity: truthiness gates the passthrough
        assert agent.llm is factory.return_value

    def test_factory_failure_falls_back_to_the_default_openai_llm(self):
        fallback = MagicMock()
        with (
            patch(f"{GENERATION}.OpenAI", return_value=MagicMock()),
            patch(f"{GENERATION}.SUPPORTED_LLM_PROVIDERS", {"openai": MagicMock(side_effect=RuntimeError("no"))}),
            patch(f"{GENERATION}.OpenAiLLM", return_value=fallback),
        ):
            agent = GraphAgent(base_config(_nodes(), "greeting"))
        assert agent.llm is fallback


# ---------------------------------------------------------------------------
# judgments
# ---------------------------------------------------------------------------


class TestJudgments:
    async def test_check_for_completion_parses_and_times_the_judgment(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        agent.conversation_completion_llm = MagicMock()
        agent.conversation_completion_llm.generate = AsyncMock(return_value=('{"hangup": "Yes"}', {}))
        hangup, metadata = await agent.check_for_completion([{"role": "user", "content": "bye"}], "judge")
        assert hangup == {"hangup": "Yes"} and "latency_ms" in metadata

    async def test_check_for_completion_failure_keeps_talking(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        agent.conversation_completion_llm = MagicMock()
        agent.conversation_completion_llm.generate = AsyncMock(side_effect=RuntimeError("down"))
        assert await agent.check_for_completion([], "judge") == ({"hangup": "No"}, {})

    async def test_check_for_voicemail_appends_the_json_instruction(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        agent.voicemail_llm = MagicMock()
        agent.voicemail_llm.generate = AsyncMock(return_value=('{"is_voicemail": "Yes"}', {}))
        result, metadata = await agent.check_for_voicemail("leave a message after the beep")
        assert result == {"is_voicemail": "Yes"} and "latency_ms" in metadata
        prompt = agent.voicemail_llm.generate.call_args.args[0]
        assert '"is_voicemail": "Yes" or "No"' in prompt[0]["content"]

    async def test_check_for_voicemail_failure_answers_no(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        agent.voicemail_llm = MagicMock()
        agent.voicemail_llm.generate = AsyncMock(side_effect=RuntimeError("down"))
        assert await agent.check_for_voicemail("hello") == ({"is_voicemail": "No"}, {})


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerate:
    _PICK_BYE: tuple = ("goodbye", None, 5.0, [{"role": "system", "content": "r"}], [], "bye", 0.8, None)
    _STAY: tuple = (None, None, 5.0, None, None, None, None, None)

    async def test_transition_then_static_node_yields_playback_chunk(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        with patch.object(
            agent, "decide_next_node_with_functions", new_callable=AsyncMock, return_value=self._PICK_BYE
        ):
            out = await _collect(agent.generate([{"role": "user", "content": "bye"}]))
        routing = [o["routing_info"] for o in out if isinstance(o, dict) and "routing_info" in o]
        assert routing[0]["transitioned"] is True and routing[0]["routing_type"] == "llm"
        static = [o for o in out if isinstance(o, dict) and "static_message" in o]
        assert static and static[0]["static_message"] == "Goodbye!" and static[0]["static_audio_hash"]

    async def test_stay_speaks_the_active_node_with_messages_signal(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        with patch.object(agent, "decide_next_node_with_functions", new_callable=AsyncMock, return_value=self._STAY):
            out = await _collect(agent.generate([{"role": "user", "content": "hello"}], meta_info={}))
        messages = next(o["messages"] for o in out if isinstance(o, dict) and "messages" in o)
        assert messages[0]["role"] == "system" and "## Conversation History" in messages[0]["content"]
        assert agent._test_llm.generate_stream.called

    async def test_detected_language_and_silence_are_recorded(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        with patch.object(agent, "decide_next_node_with_functions", new_callable=AsyncMock, return_value=self._STAY):
            await _collect(
                agent.generate([{"role": "user", "content": "[silence] ..."}], meta_info={"detected_language": "hi"})
            )
        assert agent.context_data["detected_language"] == "hi"
        assert agent._silence_repeats == 1

    async def test_hold_blocks_routing_until_first_delivery(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        agent._advance_to_node("greeting", 0)  # freshly entered -> first response undelivered
        with patch.object(agent, "_decide_next_node_llm", new_callable=AsyncMock) as decide:
            out = await _collect(agent.generate([{"role": "user", "content": "hello"}]))
        decide.assert_not_called()
        routing = [o["routing_info"] for o in out if isinstance(o, dict) and "routing_info" in o]
        assert routing[-1]["routing_type"] == "hold" and routing[-1]["transitioned"] is False

    async def test_unresolvable_router_ends_the_turn_silently(self, make_agent):
        nodes = [
            {
                "id": "entry",
                "node_type": "router",
                "edges": [{"to_node_id": "x", "condition_type": "expression", "expression": expr("tier", "eq", "vip")}],
            },
            {"id": "x", "prompt": "X.", "edges": []},
        ]
        agent = make_agent(base_config(nodes, "entry", context_data={"tier": "basic"}))
        out = await _collect(agent.generate([{"role": "user", "content": "hi"}]))
        assert not any(isinstance(o, dict) and "messages" in o for o in out)
        assert any(isinstance(o, LLMStreamChunk) and o.end_of_stream for o in out)

    async def test_build_messages_failure_speaks_the_error_chunk(self, make_agent):
        agent = make_agent(base_config(_nodes(), "greeting"))
        agent.current_node_id = "ghost"  # no such node: decide stays, _build_messages raises
        out = await _collect(agent.generate([{"role": "user", "content": "hi"}], meta_info={}))
        chunk = out[-1]
        assert isinstance(chunk, LLMStreamChunk) and chunk.end_of_stream
        assert chunk.data == "An error occurred: Current node not found."


class TestGenerateEventTriggered:
    def _event_nodes(self):
        return [
            {
                "id": "waiting",
                "prompt": "Wait.",
                "edges": [{"to_node_id": "speak", "condition_type": "event", "event_name": "link_opened"}],
            },
            {
                "id": "speak",
                "prompt": "Speak.",
                "edges": [{"to_node_id": "done", "condition_type": "event", "event_name": "details_verified"}],
            },
            {"id": "done", "node_type": "static", "static_message": "All set.", "edges": []},
        ]

    async def test_event_generation_injects_the_ephemeral_hint(self, make_agent):
        agent = make_agent(base_config(self._event_nodes(), "waiting"))
        result = agent.process_event({"event": "link_opened"})
        assert result["matched"] is True
        agent._event_triggered_generation = True
        out = await _collect(agent.generate([], meta_info={}))
        routing = [o["routing_info"] for o in out if isinstance(o, dict) and "routing_info" in o]
        assert routing[0]["routing_type"] == "event" and routing[0]["event_triggered"] is True
        messages = next(o["messages"] for o in out if isinstance(o, dict) and "messages" in o)
        assert messages[-1]["content"].startswith("[Event: link_opened.")
        assert agent._event_triggered_generation is False

    async def test_event_landing_on_a_static_node_yields_its_chunk(self, make_agent):
        agent = make_agent(base_config(self._event_nodes(), "speak"))
        agent.process_event({"event": "details_verified"})
        agent._event_triggered_generation = True
        out = await _collect(agent.generate([], meta_info={}))
        assert any(isinstance(o, dict) and o.get("static_message") == "All set." for o in out)

    async def test_event_landing_on_an_unresolvable_router_ends_the_turn(self, make_agent):
        nodes = [
            {
                "id": "waiting",
                "prompt": "Wait.",
                "edges": [{"to_node_id": "dead_router", "condition_type": "event", "event_name": "go"}],
            },
            {
                "id": "dead_router",
                "node_type": "router",
                "edges": [{"to_node_id": "x", "condition_type": "expression", "expression": expr("tier", "eq", "vip")}],
            },
            {"id": "x", "prompt": "X.", "edges": []},
        ]
        agent = make_agent(base_config(nodes, "waiting", context_data={"tier": "basic"}))
        agent.process_event({"event": "go"})
        agent._event_triggered_generation = True
        out = await _collect(agent.generate([], meta_info={}))
        assert any(isinstance(o, LLMStreamChunk) and o.end_of_stream for o in out)
        assert not any(isinstance(o, dict) and "messages" in o for o in out)
