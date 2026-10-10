"""Graph definition model, validator, structural dry-run and deploy mapping (spec 0078).

Moved verbatim from `voiceai/platform/graphs.py` with the graph records, because a
family whose endpoints call its own engine has to move as one unit — see the spec.
The validator mirrors the engine's hard rules (voiceai/models.py GraphNode: routers
never speak, need an unconditional catch-all, no event edges) plus structural
checks, so the builder catches breakage before deploy. The dry-run is a
deterministic structural walk, not LLM routing: it follows edges by tier order and
reports the path, a transcript preview and loops.

The only change from the legacy file is the logger: Rule 3 requires the single
`otobaai` logger, and the legacy `helpers.logger_config` call came with it. No
behaviour changed — `test_engine_parity.py` pins that.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from voiceai.common.validation import ValidationResult
from voiceai.modules.automation.constants import (
    AGENT_TYPE_OTHER,
    DEFAULT_PRIORITY,
    MAX_DRY_RUN_STEPS,
    TIER_RANK,
)

__all__ = [
    "DryRunResult",
    "DryRunTurn",
    "GraphCanvasEdge",
    "GraphCanvasNode",
    "GraphDefinition",
    "ValidationResult",
    "dry_run",
    "graph_to_agent_payload",
    "validate_definition",
]


class GraphCanvasEdge(BaseModel):
    """One outgoing edge of a graph node."""

    to_node_id: str = Field(..., min_length=1)
    condition: str = ""
    label: str | None = None
    condition_type: Literal["llm", "expression", "unconditional", "event"] = "llm"
    expression: dict[str, Any] | None = None
    event_name: str | None = None
    priority: int | None = None


class GraphCanvasNode(BaseModel):
    """One node of a graph."""

    id: str = Field(..., min_length=1)
    node_type: Literal["llm", "static", "router"] = "llm"
    description: str | None = None
    prompt: str = ""
    static_message: str | None = None
    repeat_after_silence_seconds: float | None = None
    edges: list[GraphCanvasEdge] = Field(default_factory=list)


class GraphDefinition(BaseModel):
    """A whole graph: its nodes, edges and start node."""

    agent_information: str = ""
    start_node_id: str = ""
    routing_model: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    nodes: list[GraphCanvasNode] = Field(default_factory=list)


class DryRunTurn(BaseModel):
    """One turn the dry-run expects the conversation to produce."""

    node: str
    text: str


class DryRunResult(BaseModel):
    """The outcome of the structural dry-run walk."""

    path: list[str] = Field(default_factory=list)
    transcript_preview: list[DryRunTurn] = Field(default_factory=list)
    steps: int = 0
    loop_detected: bool = False


def validate_definition(definition: GraphDefinition) -> ValidationResult:
    """Check engine rules + structure. Errors break deploys; warnings do not."""
    errors: list[str] = []
    warnings: list[str] = []
    nodes = definition.nodes

    if not nodes:
        return ValidationResult(valid=False, errors=["graph has no nodes"])

    ids = [node.id for node in nodes]
    seen: set[str] = set()
    for node_id in ids:
        if node_id in seen:
            errors.append(f"duplicate node id '{node_id}'")
        seen.add(node_id)
    by_id = {node.id: node for node in nodes}

    if definition.start_node_id not in by_id:
        errors.append(f"start node '{definition.start_node_id}' does not exist")

    for node in nodes:
        for edge in node.edges:
            if edge.to_node_id not in by_id:
                errors.append(f"node '{node.id}' points to unknown node '{edge.to_node_id}'")
            if edge.to_node_id == node.id:
                warnings.append(f"node '{node.id}' links to itself")
            if edge.condition_type == "event" and not edge.event_name:
                warnings.append(f"node '{node.id}' has an event edge without an event name")
        if node.node_type == "router":
            if node.prompt or node.static_message:
                errors.append(f"router node '{node.id}' must not set a prompt or static_message; it never speaks")
            if any(edge.condition_type == "event" for edge in node.edges):
                errors.append(f"router node '{node.id}' cannot use event edges; calls never rest on a router")
            if not any(edge.condition_type == "unconditional" for edge in node.edges):
                errors.append(f"router node '{node.id}' needs an unconditional catch-all edge")
        elif node.node_type == "static":
            if not (node.static_message or "").strip():
                warnings.append(f"static node '{node.id}' has no static_message to play")
        else:
            if not node.prompt.strip():
                warnings.append(f"llm node '{node.id}' has an empty prompt")

    # Reachability from the start node.
    if definition.start_node_id in by_id:
        reachable: set[str] = set()
        stack = [definition.start_node_id]
        while stack:
            current = stack.pop()
            if current in reachable or current not in by_id:
                continue
            reachable.add(current)
            stack.extend(edge.to_node_id for edge in by_id[current].edges)
        for node_id in ids:
            if node_id not in reachable:
                warnings.append(f"node '{node_id}' is unreachable from the start node")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _edge_rank(edge: GraphCanvasEdge) -> tuple[int, int]:
    tier = TIER_RANK.get(edge.condition_type, 1)
    default = DEFAULT_PRIORITY.get(edge.condition_type, 100)
    return (tier, edge.priority if edge.priority is not None else default)


def dry_run(definition: GraphDefinition, max_steps: int = MAX_DRY_RUN_STEPS) -> DryRunResult:
    """Walk the graph from the start node following tier order. Deterministic."""
    by_id = {node.id: node for node in definition.nodes}
    result = DryRunResult()
    current = definition.start_node_id
    while current and current in by_id and result.steps < max_steps:
        if current in result.path:
            result.loop_detected = True
            break
        node = by_id[current]
        result.path.append(current)
        result.steps += 1
        if node.node_type == "static" and (node.static_message or "").strip():
            result.transcript_preview.append(DryRunTurn(node=current, text=(node.static_message or "").strip()))
        elif node.node_type == "llm":
            text = node.prompt.strip()[:120] or "[generates a reply]"
            result.transcript_preview.append(DryRunTurn(node=current, text=text))
        if not node.edges:
            break
        nxt = sorted(node.edges, key=_edge_rank)[0].to_node_id
        if nxt in result.path:
            result.loop_detected = True
            break
        current = nxt
    return result


def graph_to_agent_payload(graph_name: str, definition: GraphDefinition, agent_name: str) -> dict[str, Any]:
    """Build a POST /agent body whose task runs this graph on the engine."""
    nodes = []
    for node in definition.nodes:
        engine_node: dict[str, Any] = {
            "id": node.id,
            "node_type": node.node_type,
            "prompt": node.prompt,
            "edges": [
                {
                    "to_node_id": edge.to_node_id,
                    "condition": edge.condition,
                    **({"label": edge.label} if edge.label else {}),
                    **({"condition_type": edge.condition_type} if edge.condition_type != "llm" else {}),
                    **({"expression": edge.expression} if edge.expression else {}),
                    **({"event_name": edge.event_name} if edge.event_name else {}),
                    **({"priority": edge.priority} if edge.priority is not None else {}),
                }
                for edge in node.edges
            ],
        }
        if node.description:
            engine_node["description"] = node.description
        if node.static_message:
            engine_node["static_message"] = node.static_message
        if node.repeat_after_silence_seconds is not None:
            engine_node["repeat_after_silence_seconds"] = node.repeat_after_silence_seconds
        nodes.append(engine_node)

    llm_config: dict[str, Any] = {
        "agent_information": definition.agent_information or f"Agent {graph_name}.",
        "nodes": nodes,
        "current_node_id": definition.start_node_id,
    }
    if definition.routing_model:
        llm_config["routing_model"] = definition.routing_model
    return {
        "agent_config": {
            "agent_name": agent_name,
            "agent_type": AGENT_TYPE_OTHER,
            "tasks": [
                {
                    "task_type": "conversation",
                    "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm", "synthesizer"]]},
                    "tools_config": {
                        "transcriber": {"provider": "deepgram", "language": "en", "stream": True},
                        "llm_agent": {
                            "agent_type": "graph_agent",
                            "agent_flow_type": "streaming",
                            "llm_config": llm_config,
                        },
                        "synthesizer": {"provider": "elevenlabs", "stream": True, "audio_format": "wav"},
                    },
                    "task_config": {"check_if_user_online": True},
                }
            ],
        },
        "agent_prompts": {"task_1": {"system_prompt": definition.agent_information or f"Agent {graph_name}."}},
    }
