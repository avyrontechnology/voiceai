"""Characterization: the never-constructed legacy graph brain (spec 0002, step A7).

`GraphBasedConversationAgent` is import parity only — no engine dispatch path builds
one — but its move to `brains/legacy_graph.py` must not silently change what the class
would do, so these pin the moved logic offline with a fake classification LLM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.modules.agents.brains.legacy_graph import Graph, GraphBasedConversationAgent, Node

CONVERSATION_DATA = {
    "root": {
        "label": "intro",
        "content": [{"text": "Hello {name}!", "audio": "seed"}],
        "prompt": "Classify.###Examples with {name}",
        "is_root": True,
        "children": ["yes"],
    },
    "yes": {
        "label": "yes",
        "content": [{"text": "Great.", "audio": "a2"}],
        "prompt": "Terminal.",
        "is_root": False,
        "children": [],
    },
}

CONTEXT = {"recipient_data": {"name": "Rahul"}}


def _agent(preprocessed=True):
    agent = GraphBasedConversationAgent(MagicMock(), prompts=None, context_data=CONTEXT, preprocessed=preprocessed)
    agent.load_prompts_and_create_graph(CONVERSATION_DATA)
    return agent


class TestGraph:
    def test_nodes_wire_root_and_children_with_substituted_examples(self):
        graph = Graph(CONVERSATION_DATA, context_data=CONTEXT)
        assert isinstance(graph.root, Node)
        assert graph.root.node_label == "intro"
        assert [child.node_id for child in graph.root.children or []] == ["yes"]
        # The ###Examples half of the prompt is context-substituted at build time.
        assert graph.graph["root"].prompt == "Classify.###Examples with Rahul"

    def test_remove_node_is_the_legacy_print_stub(self, capsys):
        graph = Graph(CONVERSATION_DATA, context_data=CONTEXT)
        root: Node = graph.root  # type: ignore[assignment] # narrowed by the pin above
        graph.remove_node(root, (root.children or [])[0])
        assert "Not yet implemented" in capsys.readouterr().out


class TestGraphBasedConversationAgent:
    def test_audio_pair_substitution_rewrites_text_and_hash(self):
        agent = _agent()
        pair = agent._get_audio_text_pair(agent.graph.root)
        assert pair["text"] == "Hello Rahul!"
        assert pair["audio"] != "seed" and len(pair["audio"]) == 32

    async def test_intro_then_classification_step(self):
        agent = _agent()
        first = await agent._get_next_preprocessed_step([])
        assert first["text"] == "Hello Rahul!"
        assert agent.conversation_intro_done is True

        agent.llm.generate = AsyncMock(return_value='{"classification_label": " YES "}')
        step = await agent._get_next_preprocessed_step(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "yes please"}]
        )
        assert step["text"] == "Great."
        assert agent.current_node_interim.node_id == "yes"
        agent.update_current_node()
        assert agent.current_node.node_id == "yes"

    async def test_unmatched_label_answers_none(self):
        agent = _agent()
        agent.conversation_intro_done = True
        agent.llm.generate = AsyncMock(return_value='{"classification_label": "nope"}')
        assert await agent._get_next_preprocessed_step([{"role": "user", "content": "?"}]) is None

    async def test_generate_on_a_leaf_yields_audio_then_end_of_conversation(self):
        agent = _agent()
        agent.update_current_node()  # still root; move manually to the leaf
        agent.current_node = agent.graph.graph["yes"]
        with patch("asyncio.sleep", new=AsyncMock()):
            out = [chunk async for chunk in agent.generate([])]
        assert out[0]["text"] == "Great."
        assert out[-1] == "<end_of_conversation>"

    async def test_generate_swallows_errors_like_the_legacy_code(self):
        agent = GraphBasedConversationAgent(MagicMock(), prompts=None, context_data=CONTEXT)
        # No graph loaded: the legacy code logs the failure and yields nothing.
        out = [chunk async for chunk in agent.generate([])]
        assert out == []
