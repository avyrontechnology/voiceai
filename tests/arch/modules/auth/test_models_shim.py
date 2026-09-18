"""Superset-shim canary for the legacy ``voiceai.platform.models`` surface (spec 0005, C2).

``PRE_MOVE_PUBLIC_NAMES`` is the recorded ``dir(voiceai.platform.models)`` snapshot,
measured on the pre-C2 tree right before the auth schema moved to
``voiceai.modules.auth.models``. The shim must remain a SUPERSET of that snapshot
forever: direct importers (routers, quickstart, agent_types) picked up these names
transitively, so a name dropping out is a silent break in legacy code. New names may
appear; recorded names may not disappear. A second pin asserts every moved auth name
is the identical object in both homes.
"""

from __future__ import annotations

import voiceai.modules.auth.models as new_models
import voiceai.platform.models as legacy_models

# Recorded pre-move snapshot — do not edit except through a spec.
PRE_MOVE_PUBLIC_NAMES = frozenset(
    {
        "ALL_SCOPES",
        "AcceptInviteRequest",
        "AddMemberRequest",
        "Any",
        "ApiKey",
        "ApiKeyListResponse",
        "AssignNumberRequest",
        "AttachKBRequest",
        "AuthEvent",
        "AuthEventListResponse",
        "AuthMeResponse",
        "BaseModel",
        "Batch",
        "BatchEntry",
        "BatchListResponse",
        "BatchStats",
        "BatchStatus",
        "CallingHours",
        "CampaignEntry",
        "CampaignStats",
        "ChangePasswordRequest",
        "CreateApiKeyRequest",
        "CreateApiKeyResponse",
        "CreateBatchRequest",
        "CreateCampaignRequest",
        "CreateGraphRequest",
        "CreateIntegrationRequest",
        "CreateInviteResponse",
        "CreateKBRequest",
        "CreatePhoneNumberRequest",
        "CreateSubAccountRequest",
        "CreateToolRequest",
        "CreateVoiceRequest",
        "CreateWebhookRequest",
        "CreateWorkflowRequest",
        "DeletedResponse",
        "Dict",
        "EMAIL_PATTERN",
        "Enum",
        "Execution",
        "ExecutionListResponse",
        "ExecutionStats",
        "ExecutionStatus",
        "Field",
        "GraphDoc",
        "GraphListResponse",
        "GraphVersion",
        "GraphVersionListResponse",
        "InboundConfig",
        "Integration",
        "IntegrationListResponse",
        "Invite",
        "InviteListResponse",
        "InviteRequest",
        "KBListResponse",
        "KBSource",
        "KBSourceType",
        "KnowledgeBase",
        "LatencyBreakdown",
        "LatencyBucket",
        "LatencyStats",
        "LedgerEntry",
        "LedgerListResponse",
        "List",
        "Literal",
        "LoginRequest",
        "MASKED_SECRET",
        "Member",
        "NodeReport",
        "NotificationPrefs",
        "Optional",
        "Organization",
        "PhoneNumber",
        "PhoneNumberListResponse",
        "PhoneNumberProvider",
        "ROLE_RANK",
        "ROLE_SCOPES",
        "ResetResponse",
        "SessionRecord",
        "SetRoleRequest",
        "SignupRequest",
        "SimulateCallRequest",
        "SubAccount",
        "SubAccountListResponse",
        "Template",
        "TemplateListResponse",
        "TemplateSummary",
        "TestRunRequest",
        "Tool",
        "ToolKind",
        "ToolListResponse",
        "TopUpRequest",
        "TranscriptTurn",
        "UpdateGraphRequest",
        "UpdateInboundRequest",
        "UpdateIntegrationRequest",
        "UpdateOrganizationRequest",
        "UpdateWorkflowRequest",
        "User",
        "UserListResponse",
        "UserResponse",
        "UserRole",
        "VectorStoreConfig",
        "VoiceEntry",
        "VoiceListResponse",
        "Wallet",
        "Webhook",
        "WebhookListResponse",
        "WorkflowCampaign",
        "WorkflowCampaignListResponse",
        "WorkflowCampaignStatus",
        "WorkflowDoc",
        "WorkflowListResponse",
        "WorkflowRun",
        "WorkflowRunListResponse",
        "WorkflowVersion",
        "WorkflowVersionListResponse",
        "WsTicketResponse",
        "datetime",
        "mask_secrets",
        "new_id",
        "timezone",
        "utcnow",
        "uuid4",
    }
)

#: Moved auth names that must be identical objects in both homes.
MOVED_IDENTITIES = (
    "ALL_SCOPES",
    "ROLE_RANK",
    "ROLE_SCOPES",
    "ApiKey",
    "AuthEvent",
    "Invite",
    "SessionRecord",
    "User",
    "UserRole",
)

#: Shared helpers the shim re-exports from their canonical homes
#: (legacy name, module path, canonical name).
SHARED_IDENTITIES = (
    ("new_id", "voiceai.common.ids", "new_id"),
    ("utcnow", "voiceai.common.datetime_utils", "utc_now"),
    ("EMAIL_PATTERN", "voiceai.common.constants", "EMAIL_PATTERN"),
)


def test_legacy_surface_stays_a_superset_of_the_snapshot() -> None:
    """Recorded names may not disappear from the legacy module."""
    current = {name for name in dir(legacy_models) if not name.startswith("_")}
    assert PRE_MOVE_PUBLIC_NAMES <= current, sorted(PRE_MOVE_PUBLIC_NAMES - current)


def test_moved_names_are_identical_objects_in_both_homes() -> None:
    """The shim re-exports, never copies (mutating one side shows on the other)."""
    import importlib

    for name in MOVED_IDENTITIES:
        assert getattr(legacy_models, name) is getattr(new_models, name), name
    for legacy_name, module_path, canonical_name in SHARED_IDENTITIES:
        module = importlib.import_module(module_path)
        assert getattr(legacy_models, legacy_name) is getattr(module, canonical_name), legacy_name
