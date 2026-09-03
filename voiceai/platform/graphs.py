"""Graph-agent definition model, validator, structural dry-run and deploy mapping.

The validator mirrors the engine's hard rules (voiceai/models.py GraphNode:
routers never speak, need an unconditional catch-all, no event edges) plus
structural checks, so the builder catches breakage before deploy. The
dry-run is a deterministic structural walk, not LLM routing: it follows
edges by tier order and reports the path, a transcript preview and loops.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

MAX_DRY_RUN_STEPS = 20

# Engine evaluation tiers: expressions first, LLM intent next, unconditional last.
_TIER_RANK = {"expression": 0, "llm": 1, "unconditional": 2, "event": 1}
_DEFAULT_PRIORITY = {"expression": 0, "unconditional": 0, "llm": 100, "event": 100}


class GraphCanvasEdge(BaseModel):
    to_node_id: str = Field(..., min_length=1)
    condition: str = ""
    label: Optional[str] = None
    condition_type: Literal["llm", "expression", "unconditional", "event"] = "llm"
    expression: Optional[Dict[str, Any]] = None
    event_name: Optional[str] = None
    priority: Optional[int] = None


class GraphCanvasNode(BaseModel):
    id: str = Field(..., min_length=1)
    node_type: Literal["llm", "static", "router"] = "llm"
    description: Optional[str] = None
    prompt: str = ""
    static_message: Optional[str] = None
    repeat_after_silence_seconds: Optional[float] = None
    edges: List[GraphCanvasEdge] = Field(default_factory=list)


class GraphDefinition(BaseModel):
    agent_information: str = ""
    start_node_id: str = ""
    routing_model: Optional[str] = None
    variables: Dict[str, str] = Field(default_factory=dict)
    nodes: List[GraphCanvasNode] = Field(default_factory=list)


class ValidationResult(BaseModel):
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DryRunTurn(BaseModel):
    node: str
    text: str


class DryRunResult(BaseModel):
    path: List[str] = Field(default_factory=list)
    transcript_preview: List[DryRunTurn] = Field(default_factory=list)
    steps: int = 0
    loop_detected: bool = False


def validate_definition(definition: GraphDefinition) -> ValidationResult:
    """Check engine rules + structure. Errors break deploys; warnings do not."""
    errors: List[str] = []
    warnings: List[str] = []
    nodes = definition.nodes

    if not nodes:
        return ValidationResult(valid=False, errors=["graph has no nodes"])

    ids = [node.id for node in nodes]
    seen: set = set()
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
        reachable: set = set()
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


def _edge_rank(edge: GraphCanvasEdge) -> tuple:
    tier = _TIER_RANK.get(edge.condition_type, 1)
    default = _DEFAULT_PRIORITY.get(edge.condition_type, 100)
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
            result.transcript_preview.append(DryRunTurn(node=current, text=node.static_message.strip()))  # type: ignore[arg-type]
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


def graph_to_agent_payload(graph_name: str, definition: GraphDefinition, agent_name: str) -> Dict[str, Any]:
    """Build a POST /agent body whose task runs this graph on the engine."""
    nodes = []
    for node in definition.nodes:
        engine_node: Dict[str, Any] = {
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

    llm_config: Dict[str, Any] = {
        "agent_information": definition.agent_information or f"Agent {graph_name}.",
        "nodes": nodes,
        "current_node_id": definition.start_node_id,
    }
    if definition.routing_model:
        llm_config["routing_model"] = definition.routing_model
    return {
        "agent_config": {
            "agent_name": agent_name,
            "agent_type": "other",
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
