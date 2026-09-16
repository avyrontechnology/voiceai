"""Split mechanics for the graph brain (spec 0002, step A7).

The 1,501-line `voiceai/agent_types/graph_agent.py` exceeded the 1,500-line hard cap and
was split into `brains/graph/*`. The behavior pins live in the legacy graph suites
(rewired to the new patch namespaces in the same commit); these tests pin the mechanics:
the line caps hold, the facade keeps the monolith's full method surface, the patched
lookups live in `generation`, and the shims answer the same objects.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import voiceai.agent_types.graph_agent as legacy_graph_agent
import voiceai.agent_types.graph_based_conversational_agent as legacy_graph_based
from voiceai.modules.agents.brains import graph, legacy_graph
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.brains.graph import GraphAgent, _DETERMINISTIC_REASONING_PREFIX, generation

REPO_ROOT = Path(__file__).resolve().parents[6]
MODULES_ROOT = REPO_ROOT / "voiceai" / "modules"
GRAPH_PACKAGE_DIR = Path(graph.__file__).parent

#: The spec's hard cap (no file under the new architecture may reach the monolith's size)
#: and the A7 split-rule target for the graph collaborators.
HARD_LINE_CAP = 1500
SPLIT_TARGET_CAP = 800

#: The monolith's full method surface, recorded pre-split — the facade keeps every name.
MONOLITH_METHODS = (
    "__init__",
    "_initialize_llm",
    "_extract_rag_collections",
    "_extract_similarity_top_k",
    "initialize_rag_configs",
    "_initialize_global_rag_config",
    "_routing_create",
    "_init_routing_client",
    "check_for_completion",
    "check_for_voicemail",
    "_edge_function_name",
    "_build_transition_tools_for_edges",
    "_build_transition_tools",
    "_get_edge_by_function_name_from_edges",
    "_get_edge_by_function_name",
    "_classify_edges",
    "_evaluate_deterministic_edges",
    "process_event",
    "_compute_turn_counts",
    "_enrich_routing_context",
    "_node_type_of",
    "_match_expression_edge",
    "_catch_all_edge",
    "_catch_all_reasoning",
    "_router_hop_info",
    "_resolve_router_chain",
    "_advance_to_node",
    "mark_first_response_delivered",
    "_should_hold_for_first_delivery",
    "_hold_routing_info",
    "_end_turn_chunk",
    "_decide_next_node_llm",
    "decide_next_node_with_functions",
    "get_node_by_id",
    "_get_prompt_with_example",
    "_get_tool_choice_for_node",
    "_tools_for_node",
    "_missing_forced_function_vars",
    "_forced_function_already_called",
    "_prompt_context",
    "_build_messages",
    "_static_message_chunk",
    "generate",
)

#: The names the pinning suites patch, and the module whose namespace resolves them now.
PATCHED_LOOKUPS = ("OpenAI", "OpenAiLLM", "SUPPORTED_LLM_PROVIDERS")


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_no_module_file_reaches_the_hard_line_cap():
    """The A7 gate assertion: no file under voiceai/modules is 1500+ lines."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {_line_count(path)}"
        for path in sorted(MODULES_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts and _line_count(path) >= HARD_LINE_CAP
    ]
    assert not offenders, offenders


def test_graph_split_files_meet_the_split_target():
    """The split rule: every brains/graph/* file stays at or under 800 lines."""
    offenders = [
        f"{path.name}: {_line_count(path)}"
        for path in sorted(GRAPH_PACKAGE_DIR.glob("*.py"))
        if _line_count(path) > SPLIT_TARGET_CAP
    ]
    assert not offenders, offenders


def test_facade_keeps_the_monolith_method_surface():
    """Every method of the pre-split GraphAgent still resolves on the facade class."""
    missing = [name for name in MONOLITH_METHODS if not callable(getattr(GraphAgent, name, None))]
    assert not missing, missing


def test_patched_lookups_live_in_generation():
    """The rewritten monkeypatch targets exist where the lookups now happen."""
    for name in PATCHED_LOOKUPS:
        assert hasattr(generation, name), name


def test_shim_answers_the_same_objects():
    """The legacy paths re-export the SAME objects (identity, stronger than equality)."""
    assert legacy_graph_agent.GraphAgent is GraphAgent
    assert legacy_graph_agent._DETERMINISTIC_REASONING_PREFIX is _DETERMINISTIC_REASONING_PREFIX
    assert _DETERMINISTIC_REASONING_PREFIX == "deterministic:"
    assert legacy_graph_based.GraphBasedConversationAgent is legacy_graph.GraphBasedConversationAgent
    assert legacy_graph_based.Graph is legacy_graph.Graph
    assert legacy_graph_based.Node is legacy_graph.Node


async def test_facade_composes_through_the_new_path():
    """Constructing through the NEW package resolves an entry router offline."""
    mock_llm = MagicMock()
    mock_llm.trigger_function_call = False
    config = {
        "agent_information": "T",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "current_node_id": "entry",
        "context_data": {"tier": "vip"},
        "nodes": [
            {
                "id": "entry",
                "node_type": "router",
                "edges": [
                    {
                        "to_node_id": "vip",
                        "condition_type": "expression",
                        "expression": {
                            "logic": "and",
                            "conditions": [{"variable": "tier", "operator": "eq", "value": "vip"}],
                        },
                    },
                    {"to_node_id": "standard", "condition_type": "unconditional"},
                ],
            },
            {"id": "vip", "prompt": "VIP.", "edges": []},
            {"id": "standard", "prompt": "Std.", "edges": []},
        ],
    }
    with (
        patch("voiceai.modules.agents.brains.graph.generation.OpenAI", return_value=MagicMock()),
        patch(
            "voiceai.modules.agents.brains.graph.generation.SUPPORTED_LLM_PROVIDERS",
            {"openai": MagicMock(return_value=mock_llm)},
        ),
        patch("voiceai.modules.agents.brains.graph.generation.OpenAiLLM", return_value=MagicMock()),
    ):
        agent = GraphAgent(config)

    assert isinstance(agent, BaseAgent)
    assert agent.current_node_id == "entry"

    hops = await agent._resolve_router_chain([])
    assert agent.current_node_id == "vip"
    assert [h["current_node"] for h in hops] == ["vip"]
    assert hops[0]["reasoning"].startswith(_DETERMINISTIC_REASONING_PREFIX)
