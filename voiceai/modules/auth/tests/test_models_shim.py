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


#: Names a spec deliberately removed from the legacy surface, with the spec that
#: owns the removal. A deletion is only legal when it is listed here — an
#: *unlisted* disappearance is still a regression.
REMOVED_BY_SPEC: dict[str, str] = {
    "PhoneNumber": "spec 0074 — moved to voiceai.modules.numbers.models",
    "PhoneNumberListResponse": "spec 0074 — the numbers module's list response (NumberListResponse); the wire shape ('numbers') is unchanged",
    "CreatePhoneNumberRequest": "spec 0074 — moved to voiceai.modules.numbers.schemas",
    "KnowledgeBase": "spec 0075 — moved to voiceai.modules.knowledge.models",
    "KBSource": "spec 0075 — moved to voiceai.modules.knowledge.models",
    "KBListResponse": "spec 0075 — the knowledge module's list response (KnowledgeListResponse); the wire shape ('knowledgebases') is unchanged",
    "CreateKBRequest": "spec 0075 — CreateKnowledgeRequest in voiceai.modules.knowledge.schemas",
    "AttachKBRequest": "spec 0075 — AttachKnowledgeRequest in voiceai.modules.knowledge.schemas",
    "Webhook": "spec 0076 — moved to voiceai.modules.webhooks.models",
    "WebhookListResponse": "spec 0076 — the webhooks module's list response (WebhookListResponse); the wire shape ('webhooks') is unchanged",
    "CreateWebhookRequest": "spec 0076 — CreateWebhookRequest in voiceai.modules.webhooks.schemas",
    "Integration": "spec 0077 — moved to voiceai.modules.integrations.models",
    "IntegrationListResponse": "spec 0077 — the integrations module's list response; wire shape unchanged",
    "CreateIntegrationRequest": "spec 0077 — CreateIntegrationRequest in voiceai.modules.integrations.schemas",
    "UpdateIntegrationRequest": "spec 0077 — UpdateIntegrationRequest in voiceai.modules.integrations.schemas",
    "mask_secrets": "spec 0077 — moved to voiceai.modules.integrations.static_methods (the module\'s masking contract)",
    "MASKED_SECRET": "spec 0077 — moved to voiceai.modules.integrations.constants",
    "AssignNumberRequest": "spec 0074 — moved to voiceai.modules.numbers.schemas",
}


def test_legacy_surface_stays_a_superset_of_the_snapshot() -> None:
    """Recorded names may not disappear from the legacy module.

    Exception: a name a spec deliberately migrated out, which must be listed in
    `REMOVED_BY_SPEC` and still be importable from its new module home.
    """
    current = {name for name in dir(legacy_models) if not name.startswith("_")}
    missing = PRE_MOVE_PUBLIC_NAMES - current
    assert missing <= set(REMOVED_BY_SPEC), sorted(missing - set(REMOVED_BY_SPEC))


def test_removed_names_are_importable_from_their_new_home() -> None:
    """A migration must move the name, not just delete it."""
    from voiceai.modules.numbers import models as numbers_models
    from voiceai.modules.numbers import schemas as numbers_schemas

    assert numbers_models.PhoneNumber.__name__ == "PhoneNumber"
    assert numbers_schemas.NumberListResponse.__name__ == "NumberListResponse"
    from voiceai.modules.knowledge import models as knowledge_models
    from voiceai.modules.knowledge import schemas as knowledge_schemas

    assert knowledge_models.KnowledgeBase.__name__ == "KnowledgeBase"
    assert knowledge_models.KBSource.__name__ == "KBSource"
    assert knowledge_schemas.KnowledgeListResponse.__name__ == "KnowledgeListResponse"
    assert knowledge_schemas.CreateKnowledgeRequest.__name__ == "CreateKnowledgeRequest"
    assert knowledge_schemas.AttachKnowledgeRequest.__name__ == "AttachKnowledgeRequest"
    from voiceai.modules.webhooks import models as webhook_models
    from voiceai.modules.webhooks import schemas as webhook_schemas

    assert webhook_models.Webhook.__name__ == "Webhook"
    assert webhook_schemas.WebhookListResponse.__name__ == "WebhookListResponse"
    assert webhook_schemas.CreateWebhookRequest.__name__ == "CreateWebhookRequest"
    from voiceai.modules.integrations import models as integration_models
    from voiceai.modules.integrations import schemas as integration_schemas

    assert integration_models.Integration.__name__ == "Integration"
    assert integration_schemas.IntegrationListResponse.__name__ == "IntegrationListResponse"
    assert integration_schemas.CreateIntegrationRequest.__name__ == "CreateIntegrationRequest"
    assert integration_schemas.UpdateIntegrationRequest.__name__ == "UpdateIntegrationRequest"
    assert numbers_schemas.CreateNumberRequest.__name__ == "CreateNumberRequest"
    assert numbers_schemas.AssignNumberRequest.__name__ == "AssignNumberRequest"


def test_moved_names_are_identical_objects_in_both_homes() -> None:
    """The shim re-exports, never copies (mutating one side shows on the other)."""
    import importlib

    for name in MOVED_IDENTITIES:
        assert getattr(legacy_models, name) is getattr(new_models, name), name
    for legacy_name, module_path, canonical_name in SHARED_IDENTITIES:
        module = importlib.import_module(module_path)
        assert getattr(legacy_models, legacy_name) is getattr(module, canonical_name), legacy_name
