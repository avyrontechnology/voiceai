"""Tool and credential/integration persisted rows (specs 0029, 0056, 0059, 0081).

All models inherit `database.base.BaseFields`; collection names come from
`database.constants.Collections`. Soft deletes only (`is_active`) — no row is
ever destroyed unless a spec argues for it.

- `ToolDefinition` / `ToolExecutionLog` — shareable function + webhook tools
  and their tenant-scoped execution audit rows (spec 0029, spec 0056 Phase-1).
- `PartnerCredential` / `PartnerIntegration` / `IntegrationTemplate` —
  credential/integration state for the tools vault (spec 0056 Phase-2,
  spec 0059 auth model), extended with the 0081 owner chain
  (`owner_partner_id` / `owner_org_id` / `owner_user_id`), binding scope,
  visibility, versioning and lifecycle.
- `OrgPolicy` / `IntegrationAuditLog` — the single-enum sharing rulebook per
  org and the append-only governance trail (spec 0081, Phase-1).
- `OwnerScope` — the frozen scope object every scoped repository call takes
  explicitly (repositories never read ambient context).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.database.base import BaseFields
from voiceai.modules.tools.constants import (
    BINDING_ORG_SHARED,
    BINDING_USER_PRIVATE,
    CREDENTIAL_STATUS_ACTIVE,
    CREDENTIAL_TYPE_API_KEY,
    CREDENTIAL_TYPE_BASIC,
    CREDENTIAL_TYPE_BEARER,
    CREDENTIAL_TYPE_CUSTOM,
    CREDENTIAL_TYPE_NONE,
    CREDENTIAL_TYPE_OAUTH2,
    INTEGRATION_STATUS_ACTIVE,
    LIFECYCLE_DRAFT,
    POLICY_SHARED_WITH_OVERRIDE,
    TOOL_KIND_FUNCTION,
    VISIBILITY_PARTNER_PRIVATE,
    Binding,
    BindingPolicy,
    CredentialStatus,
    CredentialType,
    TemplateLifecycle,
    ToolKind,
    VisibilityScope,
)

__all__ = [
    "BINDING_ORG_SHARED",
    "BINDING_USER_PRIVATE",
    "CREDENTIAL_STATUS_ACTIVE",
    "CREDENTIAL_TYPE_API_KEY",
    "CREDENTIAL_TYPE_BASIC",
    "CREDENTIAL_TYPE_BEARER",
    "CREDENTIAL_TYPE_CUSTOM",
    "CREDENTIAL_TYPE_NONE",
    "CREDENTIAL_TYPE_OAUTH2",
    "Binding",
    "BindingPolicy",
    "CredentialStatus",
    "CredentialType",
    "INTEGRATION_STATUS_ACTIVE",
    "IntegrationAuditLog",
    "IntegrationTemplate",
    "OrgPolicy",
    "OwnerScope",
    "PartnerCredential",
    "PartnerIntegration",
    "TemplateLifecycle",
    "ToolDefinition",
    "ToolExecutionLog",
    "TOOL_KIND_FUNCTION",
    "ToolKind",
    "VISIBILITY_PARTNER_PRIVATE",
    "VisibilityScope",
]


@dataclass(frozen=True)
class OwnerScope:
    """Who a scoped repository call acts for (spec 0081, Phase-1).

    Repositories take this explicitly — they never read ambient tenant
    context, so every owner filter in a query is visible at the call site.
    Construct from plain strings only: callers map their own principal's
    fields here without importing another module (the layer matrix forbids
    tools → auth imports, so there is deliberately no `from_principal`).

    Attributes:
        partner_id: Billing and isolation boundary (the tenant id).
        org_id: Sharing boundary inside the partner, or `None` for
            partner-level rows (templates).
        user_id: BYOK owner, or `None` for org-shared / partner rows.
    """

    partner_id: str
    org_id: str | None = None
    user_id: str | None = None

    @classmethod
    def from_owner(cls, partner_id: str, org_id: str | None = None, user_id: str | None = None) -> OwnerScope:
        """Build a scope from plain owner strings.

        Args:
            partner_id: The partner (tenant) id — always required.
            org_id: The org id, or `None` for partner-level rows.
            user_id: The user id, or `None` for shared rows.

        Returns:
            The frozen scope (no validation — repositories treat a missing
            `partner_id` as a wiring bug via `ensure_owner_scope`).
        """
        return cls(partner_id=partner_id, org_id=org_id, user_id=user_id)


def _default_allowed_bindings() -> list[str]:
    """Return the most-restrictive binding default (spec 0081).

    New templates admit private use only until a curator widens them —
    org-shared must be chosen deliberately, never by default.

    Returns:
        A fresh single-item list (no shared mutable default).
    """
    return [BINDING_USER_PRIVATE]


class ToolDefinition(BaseFields):
    """One shareable tool row.

    System rows (`tenant_id="system"`) are the curated internal tools every tenant
    reads; tenant rows are isolated CRUD. The seeder stamps the tenant; readers
    never write.

    Attributes:
        tool_id: Natural key (`{kind}:{name}`, pinned as `id`).
        kind: `function` (tenant or internal), `webhook`, or `internal`
            (system-only behavior tools: hangup, transfer, knowledge search).
        name: Human label, unique per tenant scope.
        description: What the tool does (surfaced to the LLM + builder).
        parameters: JSON-Schema parameter contract (OpenAI function shape).
        url: Endpoint binding for `function`/`webhook` kinds (validated on
            attach, never fetched here); `None` for pure-internal behaviors.
        method: HTTP method for the binding.
        auth_ref: Secret-store reference (never a raw secret — secrets live
            in environment/secret store, rows carry the pointer).
        timeout_s: Per-call timeout budget.
        deprecated: Hidden from pickers, still resolvable (old agents read on).
        requires_tenant_key: BYOK extension point (reserved, unenforced in v1).
        tools_version: Seed revision that wrote the row (drift visibility).
    """

    tool_id: str = Field(..., min_length=1)
    kind: ToolKind = TOOL_KIND_FUNCTION
    name: str = Field(..., min_length=1)
    description: str = ""
    parameters: dict = Field(default_factory=dict)  # why: JSON-Schema contracts are free-form JSON
    url: str | None = None
    method: str = "POST"
    auth_ref: str | None = None
    timeout_s: int = 10
    params_template: dict = Field(default_factory=dict)  # why: webhook params templates are free-form JSON
    deprecated: bool = False
    requires_tenant_key: bool = False
    tools_version: int = 1
    tenant_id: str | None = SYSTEM_TENANT_ID
    #: Owning org inside the tenant (spec 0081): `None` means tenant-wide
    #: (visible to every org for the P5 picker); set narrows the picker.
    owner_org_id: str | None = None


class ToolExecutionLog(BaseFields):
    """One tenant-scoped execution audit row (spec 0056, Phase-1).

    Written by `ToolExecutor` for every terminal outcome — success, upstream
    failure, and refusal (allowlist/schema/SSRF) alike — so operators can
    answer "what ran for whom" without ever seeing secrets: `arguments` is
    the `redact_secrets` copy taken before the call, and `result_summary`
    carries status codes and timings only, never bodies.

    Attributes:
        tool_id: Canonical executed id (MCP namespace already stripped).
        agent_id: Owning agent (the allowlist principal).
        arguments: Redacted call arguments (secret values replaced).
        result_summary: Short safe outcome line (never raw secrets or bodies).
        success: Whether the call answered successfully.
        execution_ms: Wall time of the guarded execution.
        error_id: Log-stitching id for failures (matches the returned result).
    """

    tool_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    arguments: dict = Field(default_factory=dict)  # why: redacted JSON args are free-form
    result_summary: str = ""
    success: bool = False
    execution_ms: int = Field(default=0, ge=0)
    error_id: str | None = None
    #: Owner chain stamped at execution time (spec 0081): `credential_version`
    #: 0 means unknown (legacy rows); P5 stamps real versions on every run.
    org_id: str | None = None
    user_id: str | None = None
    credential_id: str | None = None
    credential_version: int = Field(default=0, ge=0)


class PartnerCredential(BaseFields):
    """One encrypted partner secret row.

    The secret itself never lands here in the clear: `encrypted_data` is the
    `CredentialVault` envelope (nonce + AES-GCM ciphertext, base64) and
    `key_version` names the key generation that sealed it, so a rotation can
    tell stale rows from current ones. `expires_at` bounds OAuth tokens; `None`
    means a non-expiring API key.

    Attributes:
        provider: Partner name (`github`, `slack`, … — a code/DB constant at
            call sites, never free user input into a query).
        credential_type: `none`|`api_key`|`bearer`|`basic`|`oauth2`|`custom`.
        auth_config: NON-secret placement (api_key `{location, name}`, basic
            `{username}` — a `password`/`token`/`secret` key here is refused
            at the service boundary, never stored).
        encrypted_data: Vault envelope (opaque ciphertext — never logged).
        key_version: Key generation that sealed the envelope (`>= 1`).
        expires_at: Token expiry, or `None` for non-expiring keys.
        tenant_id: Owner tenant, stamped on write; `None` never queried.
    """

    provider: str = Field(..., min_length=1)
    credential_type: CredentialType = CREDENTIAL_TYPE_API_KEY
    auth_config: dict[str, Any] = Field(default_factory=dict)
    encrypted_data: str = Field(..., min_length=1)
    key_version: int = Field(..., ge=1)
    expires_at: datetime | None = None
    tenant_id: str | None = None
    #: Owner chain (spec 0081): org set for both bindings, user set exactly
    #: for `user_private`. `binding=None` is the explicit legacy-unscoped
    #: state — manageable in listings, invisible to execution (re-choose at
    #: the P2 boundary, never a silent default).
    owner_org_id: str | None = None
    owner_user_id: str | None = None
    binding: Binding | None = None
    status: CredentialStatus = CREDENTIAL_STATUS_ACTIVE


class PartnerIntegration(BaseFields):
    """One linked partner integration for an agent.

    The link record: which provider an agent may call through, its runtime
    config (endpoint pointers and non-secret options — secrets stay in
    `PartnerCredential`), and the MCP protocol version the Phase-3 handshake
    negotiated (empty until then).

    Attributes:
        provider: Partner name (same vocabulary as `PartnerCredential`).
        agent_id: Agent this link belongs to.
        config: Non-secret runtime options (endpoint ids, scopes, flags).
        status: Lifecycle state (`active` by default; revocation clears it).
        negotiated_protocol_version: MCP version from the Phase-3 handshake;
            empty until negotiated.
        tenant_id: Owner tenant, stamped on write; `None` never queried.
    """

    provider: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    config: dict = Field(default_factory=dict)  # why: provider runtime options are free-form JSON
    status: str = INTEGRATION_STATUS_ACTIVE
    negotiated_protocol_version: str = ""
    tenant_id: str | None = None
    #: Link home org (spec 0081): `None` on legacy rows (mapped from the
    #: agent's org on read); P2 enforces non-null on create.
    org_id: str | None = None


class IntegrationTemplate(BaseFields):
    """One curated per-provider integration template (system rows + overrides).

    System rows (`tenant_id="system"`) ship the provider's tool template, OAuth
    discovery config, and client defaults, so a new MCP server joins via a DB
    row — no redeploy (spec 0056 goal). Tenant rows with the same `provider`
    override the curated one for that tenant only.

    Attributes:
        provider: Partner name, unique per tenant scope (the natural key).
        credential_type: Which credential shape this provider needs.
        transport: `http` (manual tool entries, spec 0059) or `mcp`
            (remote-server auto-discovery, spec 0060).
        tools_template: Curated tool shapes the provider offers.
        oauth_config: Discovery/registration options for the Phase-3 DCR flow.
        client_config: Default MCP client options (timeouts, versions).
        tenant_id: `system` for curated rows, a tenant id for overrides.
    """

    provider: str = Field(..., min_length=1)
    credential_type: CredentialType = CREDENTIAL_TYPE_API_KEY
    transport: str = Field(default="http")
    tools_template: list[dict[str, Any]] = Field(default_factory=list)  # why: curated tool shapes are free-form JSON
    oauth_config: dict = Field(default_factory=dict)  # why: DCR discovery options are free-form JSON
    client_config: dict = Field(default_factory=dict)  # why: client defaults are free-form JSON
    tenant_id: str | None = SYSTEM_TENANT_ID
    #: Visibility + ownership (spec 0081): partners may only create
    #: `partner_private` rows; `owner_partner_id="system"` marks the
    #: Otoba-admin catalogue. Legacy rows carry `None` (mapped on read).
    visibility: VisibilityScope = VISIBILITY_PARTNER_PRIVATE
    owner_partner_id: str | None = None
    #: Binding contract (spec 0081): non-empty subset of both bindings;
    #: `suggested_binding` is a hint only and must sit inside it.
    allowed_bindings: list[str] = Field(default_factory=_default_allowed_bindings)
    suggested_binding: str | None = None
    #: Versioning + lifecycle (spec 0081): publishes are immutable version
    #: bumps; `provenance` carries `{promoted_from?}` / `{seed?}`.
    version: int = Field(default=1, ge=1)
    lifecycle: TemplateLifecycle = LIFECYCLE_DRAFT
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_bindings")
    @classmethod
    def _allowed_bindings_non_empty_subset(cls, value: list[str]) -> list[str]:
        """Reject empty or unknown binding sets (spec 0081).

        Args:
            value: The submitted allowed bindings.

        Returns:
            The value unchanged when it is a non-empty subset of the
            known bindings.
        """
        known = {BINDING_ORG_SHARED, BINDING_USER_PRIVATE}
        if not value or any(item not in known for item in value):
            raise ValueError(f"allowed_bindings must be a non-empty subset of {sorted(known)}")
        return value

    @field_validator("suggested_binding")
    @classmethod
    def _suggested_binding_within_allowed(cls, value: str | None, info: Any) -> str | None:
        """Reject hints outside the allowed set (spec 0081).

        Args:
            value: The submitted hint, or `None` for no hint.
            info: Validation context carrying the sibling `allowed_bindings`.

        Returns:
            The value unchanged when `None` or inside the allowed set.
        """
        if value is None:
            return None
        allowed = (info.data or {}).get("allowed_bindings", [BINDING_USER_PRIVATE])
        if value not in allowed:
            raise ValueError("suggested_binding must sit inside allowed_bindings")
        return value


class OrgPolicy(BaseFields):
    """One org's sharing rulebook: a single enum, no invalid states (spec 0081).

    `user_choice` names a binding per connect (no org default);
    `shared_with_override` allows org reuse plus private override;
    `enforce_shared` allows org reuse only (private attempts answer 403).
    Flips suspend affected rows first (P2) — the rewrite itself is always
    explicit with actor + reason, and every flip is a ledger event.

    Attributes:
        org_id: Home org (exactly one policy row per org).
        binding_policy: The sharing rule.
        changed_by: Actor principal of the last change (identifiers only).
        reason: Why the policy changed (required on flips).
    """

    org_id: str = Field(..., min_length=1)
    binding_policy: BindingPolicy = POLICY_SHARED_WITH_OVERRIDE
    changed_by: str = ""
    reason: str = ""


class IntegrationAuditLog(BaseFields):
    """One immutable governance event (spec 0081, Phase-1).

    Execution outcomes live in `ToolExecutionLog`; everything decided —
    publish, deprecate, suspend, promote, policy flips, connects, revokes,
    syncs — lands here, append-only. Rows are never updated or deleted
    (except the governed DSAR path); `at` is the BaseFields `created_at`.

    Attributes:
        actor: Acting identity as plain strings (partner/org/user ids —
            never a client-supplied blob, mapped by the service in P2).
        action: Closed governance vocabulary (new actions need a spec note).
        target_kind: What was decided on (template, credential, link,
            org_policy, partner).
        target_id: The governed row id (identifiers only).
        reason: Why (required for suspend / flip / promote decisions).
    """

    actor: dict[str, str] = Field(default_factory=dict)
    action: str = Field(..., min_length=1)
    target_kind: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    reason: str = ""
