"""Graph engine parity (spec 0078).

The engine moved verbatim from `voiceai/platform/graphs.py`. These tests are
run against the module copy, so a future "cleanup" that changes validation or the
deploy mapping fails here rather than in production.
"""

from __future__ import annotations

from voiceai.modules.automation.engine import (
    GraphCanvasEdge,
    GraphCanvasNode,
    GraphDefinition,
    dry_run,
    graph_to_agent_payload,
    validate_definition,
)


def _linear() -> GraphDefinition:
    """A two-node linear graph: llm -> static."""
    return GraphDefinition(
        agent_information="Support bot",
        start_node_id="a",
        nodes=[
            GraphCanvasNode(id="a", node_type="llm", prompt="How can I help?", edges=[GraphCanvasEdge(to_node_id="b")]),
            GraphCanvasNode(id="b", node_type="static", static_message="Hello!"),
        ],
    )


def test_an_empty_graph_is_invalid() -> None:
    """A graph with no nodes cannot be deployed."""
    result = validate_definition(GraphDefinition())

    assert result.valid is False
    assert result.errors == ["graph has no nodes"]


def test_a_linear_graph_is_valid() -> None:
    """The happy path validates clean."""
    result = validate_definition(_linear())

    assert result.valid is True
    assert result.errors == []


def test_a_duplicate_node_id_is_an_error() -> None:
    """Two nodes with one id would make edges ambiguous."""
    definition = GraphDefinition(
        start_node_id="a",
        nodes=[GraphCanvasNode(id="a"), GraphCanvasNode(id="a")],
    )

    result = validate_definition(definition)

    assert result.valid is False
    assert any("duplicate node id" in error for error in result.errors)


def test_a_missing_start_node_is_an_error() -> None:
    """The start node must exist."""
    result = validate_definition(GraphDefinition(start_node_id="nope", nodes=[GraphCanvasNode(id="a")]))

    assert result.valid is False
    assert any("does not exist" in error for error in result.errors)


def test_a_router_may_not_speak() -> None:
    """Routers never speak — a prompt on one is an error, per the engine's rules."""
    definition = GraphDefinition(
        start_node_id="r",
        nodes=[
            GraphCanvasNode(
                id="r",
                node_type="router",
                prompt="I should not be here",
                edges=[GraphCanvasEdge(to_node_id="r", condition_type="unconditional")],
            )
        ],
    )

    result = validate_definition(definition)

    assert any("never speaks" in error for error in result.errors)


def test_a_router_needs_an_unconditional_catch_all() -> None:
    """Without one, a call could rest on the router forever."""
    definition = GraphDefinition(
        start_node_id="r",
        nodes=[GraphCanvasNode(id="r", node_type="router", edges=[GraphCanvasEdge(to_node_id="r")])],
    )

    result = validate_definition(definition)

    assert any("unconditional catch-all" in error for error in result.errors)


def test_a_router_may_not_use_event_edges() -> None:
    """Calls never rest on a router, so an event edge off one is an error."""
    definition = GraphDefinition(
        start_node_id="r",
        nodes=[
            GraphCanvasNode(
                id="r",
                node_type="router",
                edges=[
                    GraphCanvasEdge(to_node_id="r", condition_type="event", event_name="on"),
                    GraphCanvasEdge(to_node_id="r", condition_type="unconditional"),
                ],
            )
        ],
    )

    result = validate_definition(definition)

    assert any("event edges" in error for error in result.errors)


def test_an_unreachable_node_is_a_warning_not_an_error() -> None:
    """Warnings do not block a deploy, so the graph stays valid."""
    definition = GraphDefinition(
        start_node_id="a",
        nodes=[GraphCanvasNode(id="a"), GraphCanvasNode(id="orphan")],
    )

    result = validate_definition(definition)

    assert result.valid is True
    assert any("unreachable" in warning for warning in result.warnings)


def test_an_empty_prompt_is_a_warning() -> None:
    """An llm node with nothing to say is suspicious, not fatal."""
    result = validate_definition(GraphDefinition(start_node_id="a", nodes=[GraphCanvasNode(id="a")]))

    assert result.valid is True
    assert any("empty prompt" in warning for warning in result.warnings)


def test_dry_run_walks_the_path_and_previews_the_turns() -> None:
    """The dry-run follows tier order and reports the conversation it expects."""
    result = dry_run(_linear())

    assert result.path == ["a", "b"]
    assert result.steps == 2
    assert result.loop_detected is False
    assert [turn.text for turn in result.transcript_preview] == ["How can I help?", "Hello!"]


def test_dry_run_detects_a_loop() -> None:
    """A cyclic graph stops and says so rather than spinning."""
    definition = GraphDefinition(
        start_node_id="a",
        nodes=[
            GraphCanvasNode(id="a", edges=[GraphCanvasEdge(to_node_id="b")]),
            GraphCanvasNode(id="b", edges=[GraphCanvasEdge(to_node_id="a")]),
        ],
    )

    result = dry_run(definition)

    assert result.loop_detected is True


def test_dry_run_prefers_an_expression_edge_over_an_llm_one() -> None:
    """Tier order decides which edge fires first: expressions beat llm."""
    definition = GraphDefinition(
        start_node_id="a",
        nodes=[
            GraphCanvasNode(
                id="a",
                edges=[
                    GraphCanvasEdge(to_node_id="llm_first", condition_type="llm"),
                    GraphCanvasEdge(to_node_id="expr_first", condition_type="expression"),
                ],
            ),
            GraphCanvasNode(id="llm_first"),
            GraphCanvasNode(id="expr_first"),
        ],
    )

    result = dry_run(definition)

    assert result.path[1] == "expr_first"


def test_dry_run_is_bounded() -> None:
    """A long graph stops at the step cap rather than running away."""
    nodes = [GraphCanvasNode(id="n0", edges=[GraphCanvasEdge(to_node_id="n1")])]
    nodes += [GraphCanvasNode(id=f"n{i}", edges=[GraphCanvasEdge(to_node_id=f"n{i + 1}")]) for i in range(1, 40)]
    nodes.append(GraphCanvasNode(id="n40"))

    result = dry_run(GraphDefinition(start_node_id="n0", nodes=nodes))

    assert result.steps <= 20


def test_deploy_maps_to_an_agent_payload() -> None:
    """The deploy mapping builds the POST /agent body the engine runs."""
    body = graph_to_agent_payload("support", _linear(), "Support Agent")

    agent = body["agent_config"]
    assert agent["agent_name"] == "Support Agent"
    assert agent["agent_type"] == "other"
    llm_config = agent["tasks"][0]["tools_config"]["llm_agent"]["llm_config"]
    assert llm_config["current_node_id"] == "a"
    assert llm_config["agent_information"] == "Support bot"
    assert [node["id"] for node in llm_config["nodes"]] == ["a", "b"]
    assert body["agent_prompts"]["task_1"]["system_prompt"] == "Support bot"


def test_deploy_falls_back_to_a_generated_information_line() -> None:
    """An empty agent_information still produces a usable prompt."""
    definition = _linear()
    definition.agent_information = ""

    body = graph_to_agent_payload("support", definition, "Agent")

    assert body["agent_prompts"]["task_1"]["system_prompt"] == "Agent support."
