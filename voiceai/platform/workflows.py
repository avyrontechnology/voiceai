"""Workflow definitions, validation and the simulated runner.

Workflows chain agent calls with extraction, API, wait, retry, WhatsApp and
end steps. Execution is simulated (same policy as calls: no telephony, no
real HTTP): agent nodes run the simulated-call runner, API/WhatsApp steps
record would-be requests, extraction reads call variables. Deterministic
with delay_scale=0.
"""

import asyncio
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.graphs import ValidationResult
from voiceai.platform.models import (
    ExecutionStatus,
    NodeReport,
    WorkflowCampaignStatus,
    WorkflowRun,
    new_id,
    utcnow,
)
from voiceai.platform.simulation import run_simulated_call
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

MAX_WALK_STEPS = 50
MAX_WAIT_S = 30

WorkflowNodeType = Literal["start", "agent", "extraction", "api", "wait", "retry", "whatsapp", "end"]


class WorkflowNode(BaseModel):
    id: str = Field(..., min_length=1)
    type: WorkflowNodeType = "start"
    label: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    from_node: str = Field(..., min_length=1)
    to_node: str = Field(..., min_length=1)
    label: str = ""


class WorkflowDefinition(BaseModel):
    nodes: List[WorkflowNode] = Field(default_factory=list)
    edges: List[WorkflowEdge] = Field(default_factory=list)


def validate_workflow(definition: WorkflowDefinition) -> ValidationResult:
    """Structural + per-type config checks. Errors block runs."""
    errors: List[str] = []
    warnings: List[str] = []
    nodes = definition.nodes

    if not nodes:
        return ValidationResult(valid=False, errors=["workflow has no nodes"])

    ids = [node.id for node in nodes]
    if len(set(ids)) != len(ids):
        errors.append("duplicate node ids")
    by_id = {node.id: node for node in nodes}

    starts = [node for node in nodes if node.type == "start"]
    if not starts:
        errors.append("workflow needs exactly one start node")
    elif len(starts) > 1:
        errors.append("workflow must have exactly one start node")
    if not any(node.type == "end" for node in nodes):
        warnings.append("workflow has no end node; runs stop at the last step")

    for edge in definition.edges:
        if edge.from_node not in by_id:
            errors.append(f"edge starts at unknown node '{edge.from_node}'")
        if edge.to_node not in by_id:
            errors.append(f"edge leads to unknown node '{edge.to_node}'")
        if edge.label and edge.label not in ("on_success", "on_failure"):
            errors.append(f"edge '{edge.from_node}->{edge.to_node}' has unknown label '{edge.label}'")

    for node in nodes:
        config = node.config or {}
        if node.type == "agent" and not config.get("agent_id"):
            errors.append(f"agent node '{node.id}' needs config.agent_id")
        elif node.type == "api" and not config.get("url"):
            errors.append(f"api node '{node.id}' needs config.url")
        elif node.type == "retry" and (not config.get("target_node_id") or not config.get("max_attempts")):
            errors.append(f"retry node '{node.id}' needs config.target_node_id and config.max_attempts")
        elif node.type == "whatsapp" and (not config.get("to") or not config.get("template")):
            errors.append(f"whatsapp node '{node.id}' needs config.to and config.template")
        elif node.type == "extraction" and not config.get("fields"):
            errors.append(f"extraction node '{node.id}' needs config.fields")
        elif node.type == "wait" and config.get("seconds", 0) < 0:
            errors.append(f"wait node '{node.id}' needs non-negative config.seconds")

    if starts:
        reachable: set = set()
        stack = [starts[0].id]
        while stack:
            current = stack.pop()
            if current in reachable or current not in by_id:
                continue
            reachable.add(current)
            stack.extend(edge.to_node for edge in definition.edges if edge.from_node == current)
            order = [node.id for node in nodes]
            if current in order:
                nxt = order.index(current) + 1
                if nxt < len(order):
                    stack.append(order[nxt])
        for node_id in ids:
            if node_id not in reachable:
                warnings.append(f"node '{node_id}' is unreachable")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _next_node(definition: WorkflowDefinition, current_id: str, last_ok: Optional[bool]) -> Optional[WorkflowNode]:
    by_id = {node.id: node for node in definition.nodes}
    outs = [edge for edge in definition.edges if edge.from_node == current_id]
    if outs:
        if last_ok is False:
            failure = next((edge for edge in outs if edge.label == "on_failure"), None)
            if failure:
                return by_id.get(failure.to_node)
        if last_ok is True:
            success = next((edge for edge in outs if edge.label == "on_success"), None)
            if success:
                return by_id.get(success.to_node)
        plain = next((edge for edge in outs if not edge.label), None)
        return by_id.get((plain or outs[0]).to_node)
    order = [node.id for node in definition.nodes]
    if current_id in order:
        nxt = order.index(current_id) + 1
        if nxt < len(order):
            return by_id.get(order[nxt])
    return None


async def run_workflow(
    store: MemoryStore,
    workflow_id: str,
    definition: WorkflowDefinition,
    contact: Dict[str, Any],
    delay_scale: float = 0.5,
    campaign_id: Optional[str] = None,
) -> WorkflowRun:
    """Execute one workflow contact inline. Deterministic with delay_scale=0."""
    run = WorkflowRun(
        run_id=new_id("run"),
        workflow_id=workflow_id,
        campaign_id=campaign_id,
        contact=dict(contact),
        started_at=utcnow(),
    )
    variables: Dict[str, Any] = dict((contact.get("variables") or {}))
    to_number = str(contact.get("to_number") or "+910000000000")
    attempts: Dict[str, int] = {}
    last_ok: Optional[bool] = None

    starts = [node for node in definition.nodes if node.type == "start"]
    current: Optional[WorkflowNode] = starts[0] if starts else None
    steps = 0
    failed = False
    retried = False

    while current is not None and steps < MAX_WALK_STEPS:
        steps += 1
        config = current.config or {}
        if current.type == "start":
            run.reports.append(NodeReport(node_id=current.id, type="start", at=utcnow().isoformat()))  # type: ignore[arg-type]
        elif current.type == "agent":
            merged = {**variables, **(config.get("variables") or {})}
            execution = await run_simulated_call(
                store, agent_id=str(config["agent_id"]), to_number=to_number, variables=merged, delay_scale=delay_scale
            )
            ok = execution.status == ExecutionStatus.COMPLETED
            last_ok = ok
            if not ok:
                failed = True
            run.reports.append(
                NodeReport(
                    node_id=current.id,
                    type="agent",
                    status="ok" if ok else "failed",
                    detail={"execution_id": execution.execution_id, "call_status": execution.status.value},
                    at=utcnow().isoformat(),  # type: ignore[arg-type]
                )
            )
        elif current.type == "extraction":
            fields = list(config.get("fields") or [])
            values = {field: variables.get(field) for field in fields}
            variables.update({k: v for k, v in values.items() if v is not None})
            run.reports.append(
                NodeReport(
                    node_id=current.id,
                    type="extraction",
                    detail={"values": values, "method": "simulated"},
                    at=utcnow().isoformat(),
                )  # type: ignore[arg-type]
            )
        elif current.type == "api":
            run.reports.append(
                NodeReport(
                    node_id=current.id,
                    type="api",
                    detail={
                        "method": config.get("method", "POST"),
                        "url": config.get("url"),
                        "note": "simulated — no request sent",
                    },
                    at=utcnow().isoformat(),  # type: ignore[arg-type]
                )
            )
        elif current.type == "wait":
            seconds = min(float(config.get("seconds", 0)), MAX_WAIT_S)
            await asyncio.sleep(seconds * delay_scale)
            run.reports.append(
                NodeReport(node_id=current.id, type="wait", detail={"waited_s": seconds}, at=utcnow().isoformat())  # type: ignore[arg-type]
            )
        elif current.type == "retry":
            used = attempts.get(current.id, 0) + 1
            attempts[current.id] = used
            target = str(config.get("target_node_id"))
            by_id = {node.id: node for node in definition.nodes}
            if last_ok is False and used <= int(config.get("max_attempts", 1)) and target in by_id:
                retried = True
                failed = False
                run.reports.append(
                    NodeReport(
                        node_id=current.id,
                        type="retry",
                        detail={"attempt": used, "target": target, "decision": "retry"},
                        at=utcnow().isoformat(),  # type: ignore[arg-type]
                    )
                )
                current = by_id[target]
                continue
            run.reports.append(
                NodeReport(
                    node_id=current.id,
                    type="retry",
                    detail={"attempt": used, "decision": "continue"},
                    at=utcnow().isoformat(),  # type: ignore[arg-type]
                )
            )
        elif current.type == "whatsapp":
            run.reports.append(
                NodeReport(
                    node_id=current.id,
                    type="whatsapp",
                    detail={"to": config.get("to", to_number), "template": config.get("template"), "status": "logged"},
                    at=utcnow().isoformat(),  # type: ignore[arg-type]
                )
            )
        elif current.type == "end":
            run.reports.append(NodeReport(node_id=current.id, type="end", at=utcnow().isoformat()))  # type: ignore[arg-type]
            current = None
            break
        current = _next_node(definition, current.id, last_ok)

    looped = steps >= MAX_WALK_STEPS and current is not None
    # A run fails on loops, or when an agent call failed without a retry
    # consuming it. Retries that ran (even exhausted) count as handled.
    run.status = "failed" if looped or (failed and not retried) else "completed"
    run.ended_at = utcnow()
    await store.save_workflow_run(run)
    logger.info(f"Workflow run {run.run_id} finished: {run.status}")
    return run


async def run_campaign(store: MemoryStore, campaign_id: str, delay_scale: float = 0) -> Any:
    """Drive every campaign entry through its workflow. Honors stop between entries."""
    campaign = await store.get_campaign(campaign_id)
    if campaign is None or campaign.status not in (
        WorkflowCampaignStatus.DRAFT,
        WorkflowCampaignStatus.SCHEDULED,
        WorkflowCampaignStatus.RUNNING,
    ):
        return campaign
    workflow = await store.get_workflow(campaign.workflow_id)
    if workflow is None:
        return campaign
    definition = WorkflowDefinition(**workflow.definition)
    campaign.status = WorkflowCampaignStatus.RUNNING
    campaign.started_at = utcnow()
    campaign.stats.total = len(campaign.entries)
    campaign.stats.queued = len(campaign.entries)
    await store.save_campaign(campaign)

    for entry in campaign.entries:
        current = await store.get_campaign(campaign_id)
        if current is None or current.status != WorkflowCampaignStatus.RUNNING:
            campaign = current or campaign
            break
        run = await run_workflow(
            store,
            campaign.workflow_id,
            definition,
            {"to_number": entry.to_number, "variables": entry.variables},
            delay_scale,
            campaign_id=campaign_id,
        )
        campaign.stats.queued -= 1
        if run.status == "completed":
            campaign.stats.completed += 1
        else:
            campaign.stats.failed += 1
        await store.save_campaign(campaign)

    if campaign.status == WorkflowCampaignStatus.RUNNING:
        campaign.status = WorkflowCampaignStatus.COMPLETED
        campaign.ended_at = utcnow()
    await store.save_campaign(campaign)
    return campaign
