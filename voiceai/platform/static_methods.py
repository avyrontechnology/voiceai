"""Pure model builders for the platform submodule.

Deterministic constructors from validated inputs: no store, no principal,
no I/O, no environment. Services supply ids/org context explicitly so the
builders stay trivially unit-testable.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

from voiceai.platform.models import (
    ApiKey,
    Batch,
    BatchEntry,
    BatchStatus,
    CampaignEntry,
    Execution,
    GraphDoc,
    GraphVersion,
    Integration,
    KnowledgeBase,
    Member,
    PhoneNumber,
    PhoneNumberProvider,
    SubAccount,
    Tool,
    ToolKind,
    VoiceEntry,
    Webhook,
    WorkflowCampaign,
    WorkflowDoc,
    WorkflowVersion,
    new_id,
    utcnow,
)


def new_execution(
    *,
    agent_id: str,
    to_number: str,
    org_id: str,
    batch_id: Optional[str] = None,
    from_number: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
) -> Execution:
    """Build an unsaved simulated-call execution row.

    Args:
        agent_id: Owning agent.
        to_number: Destination number.
        org_id: Owning org (from the principal).
        batch_id: Optional parent batch.
        from_number: Optional caller id.
        variables: Optional template variables.

    Returns:
        The unsaved Execution.
    """
    return Execution(
        execution_id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        to_number=to_number,
        from_number=from_number,
        variables=variables or {},
        org_id=org_id,
    )


def new_batch(
    *,
    agent_id: str,
    name: str,
    entries: List[BatchEntry],
    org_id: str,
    schedule_at: Optional[Any] = None,
    calling_hours: Optional[Any] = None,
    provider: Literal["simulated", "talko"] = "simulated",
    from_number: Optional[str] = None,
) -> Batch:
    """Build an unsaved batch with stats initialized from entries.

    Args:
        agent_id: Owning agent.
        name: Batch name.
        entries: Dial entries.
        org_id: Owning org (from the principal).
        schedule_at: Optional scheduled start.
        calling_hours: Optional calling window.
        provider: Dial provider (defaults in the model).
        from_number: Optional caller id.

    Returns:
        The unsaved Batch with stats.total/queued set.
    """
    batch = Batch(
        batch_id=new_id("batch"),
        agent_id=agent_id,
        name=name,
        status=BatchStatus.SCHEDULED if schedule_at else BatchStatus.DRAFT,
        entries=list(entries),
        schedule_at=schedule_at,
        calling_hours=calling_hours,
        provider=provider,
        from_number=from_number,
        org_id=org_id,
    )
    batch.stats.total = len(batch.entries)
    batch.stats.queued = len(batch.entries)
    return batch


def new_retry_batch(*, source: Batch, failed: List[Execution], org_id: str) -> Batch:
    """Build a retry batch re-queueing failed executions.

    Args:
        source: The batch being retried.
        failed: Failed executions to re-dial.
        org_id: Owning org (from the principal).

    Returns:
        The unsaved retry Batch.
    """
    retried = Batch(
        batch_id=new_id("batch"),
        agent_id=source.agent_id,
        name=f"{source.name} (retry)",
        entries=[BatchEntry(to_number=e.to_number, variables=dict(e.variables)) for e in failed],
        calling_hours=source.calling_hours,
        provider=getattr(source, "provider", "simulated"),
        from_number=getattr(source, "from_number", None),
        org_id=org_id,
    )
    retried.stats.total = len(retried.entries)
    retried.stats.queued = len(retried.entries)
    return retried


def new_tool(
    *, agent_id: Optional[str], name: str, kind: ToolKind, config: Dict[str, Any], enabled: bool, org_id: str
) -> Tool:
    """Build an unsaved tool row.

    Args:
        agent_id: Owning agent (None for unbound tools).
        name: Tool name.
        kind: Tool kind.
        config: Tool configuration.
        enabled: Whether the tool is active.
        org_id: Owning org (from the principal).

    Returns:
        The unsaved Tool.
    """
    return Tool(
        tool_id=new_id("tool"),
        agent_id=agent_id,
        name=name,
        kind=kind,
        config=dict(config),
        enabled=enabled,
        org_id=org_id,
    )


def new_webhook(*, agent_id: Optional[str], url: str, events: List[str], enabled: bool, org_id: str) -> Webhook:
    """Build an unsaved webhook row.

    Args:
        agent_id: Owning agent (None for unbound webhooks).
        url: Delivery URL.
        events: Subscribed events.
        enabled: Whether the webhook is active.
        org_id: Owning org (from the principal).

    Returns:
        The unsaved Webhook.
    """
    return Webhook(
        webhook_id=new_id("wh"),
        agent_id=agent_id,
        url=url,
        events=list(events),
        enabled=enabled,
        org_id=org_id,
    )


def new_phone_number(*, number: str, provider: PhoneNumberProvider, country: str) -> PhoneNumber:
    """Build an unsaved phone-number row.

    Args:
        number: E.164 number.
        provider: Telephony provider.
        country: Country code.

    Returns:
        The unsaved PhoneNumber.
    """
    return PhoneNumber(number_id=new_id("num"), number=number, provider=provider, country=country)


def new_knowledge_base(*, name: str, sources: List[Any]) -> KnowledgeBase:
    """Build an unsaved knowledge-base row (status ready).

    Args:
        name: KB name.
        sources: Source references.

    Returns:
        The unsaved KnowledgeBase.
    """
    return KnowledgeBase(kb_id=new_id("kb"), name=name, sources=list(sources), status="ready")


def new_voice(*, fields: Dict[str, Any]) -> VoiceEntry:
    """Build an unsaved voice row from validated request fields.

    Args:
        fields: Validated voice payload.

    Returns:
        The unsaved VoiceEntry.
    """
    return VoiceEntry(voice_id=new_id("voice"), **fields)


def new_sub_account(*, name: str, concurrency_cap: Any) -> SubAccount:
    """Build an unsaved sub-account row.

    Args:
        name: Sub-account name.
        concurrency_cap: Concurrency cap.

    Returns:
        The unsaved SubAccount.
    """
    return SubAccount(sub_id=new_id("sub"), name=name, concurrency_cap=concurrency_cap)


def new_member(*, email: str, name: Optional[str], role: Literal["owner", "admin", "member", "viewer"]) -> Member:
    """Build a sub-account member value.

    Args:
        email: Member email.
        name: Member name (None keeps it blank).
        role: Member role.

    Returns:
        The Member value.
    """
    return Member(email=email, name=name, role=role)


# Mirrors Integration.kind in models.py (inline literal there too).
IntegrationKind = Literal[
    "twilio", "plivo", "exotel", "vobiz", "talko", "calcom", "n8n", "zapier", "sheets", "sip", "truecaller"
]


def new_integration(*, kind: IntegrationKind, name: str, config: Dict[str, Any], enabled: bool) -> Integration:
    """Build an unsaved integration row.

    Args:
        kind: Integration kind.
        name: Integration name.
        config: Integration configuration.
        enabled: Whether the integration is active.

    Returns:
        The unsaved Integration.
    """
    return Integration(
        integration_id=new_id("int"),
        kind=kind,
        name=name,
        config=dict(config),
        enabled=enabled,
    )


def new_graph(*, name: str, agent_id: Optional[str], definition: Dict[str, Any]) -> GraphDoc:
    """Build an unsaved graph document.

    Args:
        name: Graph name.
        agent_id: Optional bound agent.
        definition: Graph definition payload.

    Returns:
        The unsaved GraphDoc.
    """
    return GraphDoc(graph_id=new_id("graph"), name=name, agent_id=agent_id, definition=dict(definition))


def new_graph_version(*, graph: GraphDoc, version_number: int, note: Optional[str] = None) -> GraphVersion:
    """Snapshot a graph into a new version row.

    Args:
        graph: The graph to snapshot.
        version_number: Next version number.
        note: Optional snapshot note.

    Returns:
        The unsaved GraphVersion.
    """
    return GraphVersion(
        version_id=new_id("ver"),
        graph_id=graph.graph_id,
        version_number=version_number,
        name=graph.name,
        definition=dict(graph.definition),
        note=note,
    )


def new_workflow(*, name: str, definition: Dict[str, Any]) -> WorkflowDoc:
    """Build an unsaved workflow document.

    Args:
        name: Workflow name.
        definition: Workflow definition payload.

    Returns:
        The unsaved WorkflowDoc.
    """
    return WorkflowDoc(workflow_id=new_id("flow"), name=name, definition=dict(definition))


def new_workflow_version(*, workflow: WorkflowDoc, version_number: int, note: Optional[str] = None) -> WorkflowVersion:
    """Snapshot a workflow into a new version row.

    Args:
        workflow: The workflow to snapshot.
        version_number: Next version number.
        note: Optional snapshot note.

    Returns:
        The unsaved WorkflowVersion.
    """
    return WorkflowVersion(
        version_id=new_id("ver"),
        workflow_id=workflow.workflow_id,
        version_number=version_number,
        name=workflow.name,
        definition=dict(workflow.definition),
        note=note,
    )


def new_campaign(
    *, workflow_id: str, name: str, entries: List[CampaignEntry], calling_hours: Optional[Any]
) -> WorkflowCampaign:
    """Build an unsaved campaign with stats initialized from entries.

    Args:
        workflow_id: Parent workflow.
        name: Campaign name.
        entries: Dial entries.
        calling_hours: Optional calling window.

    Returns:
        The unsaved WorkflowCampaign with stats.total/queued set.
    """
    campaign = WorkflowCampaign(
        campaign_id=new_id("wcamp"),
        workflow_id=workflow_id,
        name=name,
        entries=list(entries),
        calling_hours=calling_hours,
    )
    campaign.stats.total = len(campaign.entries)
    campaign.stats.queued = len(campaign.entries)
    return campaign


def new_api_key_record(
    *,
    key_id: str,
    name: str,
    prefix: str,
    key_hash: str,
    scopes: List[str],
    expires_in_days: Optional[int],
    created_by: Optional[str],
) -> ApiKey:
    """Build an unsaved API-key row (hash only — never the secret).

    Args:
        key_id: Key identifier.
        name: Key name.
        prefix: Public prefix shown to the owner.
        key_hash: SHA-256 of the full secret.
        scopes: Granted scopes.
        expires_in_days: Optional lifetime in days.
        created_by: Creator user id.

    Returns:
        The unsaved ApiKey.
    """
    return ApiKey(
        key_id=key_id,
        name=name,
        prefix=prefix,
        key_hash=key_hash,
        scopes=list(scopes),
        expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
        created_by=created_by,
    )
