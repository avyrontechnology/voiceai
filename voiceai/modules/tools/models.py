
"""Tool and credential/integration persisted rows (specs 0029, 0056, 0059).

All models inherit `database.base.BaseFields`; collection names come from
`database.constants.Collections`. Soft deletes only (`is_active`) — no row is
ever destroyed unless a spec argues for it.

- `ToolDefinition` / `ToolExecutionLog` — shareable function + webhook tools
  and their tenant-scoped execution audit rows (spec 0029, spec 0056 Phase-1).
- `PartnerCredential` / `PartnerIntegration` / `IntegrationTemplate` —
  credential/integration state for the tools vault (spec 0056 Phase-2,
  spec 0059 auth model).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.database.base import BaseFields
from voiceai.modules.tools.constants import (
    CREDENTIAL_TYPE_API_KEY,
    CREDENTIAL_TYPE_BASIC,
    CREDENTIAL_TYPE_BEARER,
    CREDENTIAL_TYPE_CUSTOM,
    CREDENTIAL_TYPE_NONE,
    CREDENTIAL_TYPE_OAUTH2,
    INTEGRATION_STATUS_ACTIVE,
    TOOL_KIND_FUNCTION,
    CredentialType,
    ToolKind,
)

__all__ = [
    "CREDENTIAL_TYPE_API_KEY",
    "CREDENTIAL_TYPE_BASIC",
    "CREDENTIAL_TYPE_BEARER",
    "CREDENTIAL_TYPE_CUSTOM",
    "CREDENTIAL_TYPE_NONE",
    "CREDENTIAL_TYPE_OAUTH2",
    "CredentialType",
    "INTEGRATION_STATUS_ACTIVE",
    "IntegrationTemplate",
    "PartnerCredential",
    "PartnerIntegration",
    "ToolDefinition",
    "ToolExecutionLog",
    "TOOL_KIND_FUNCTION",
    "ToolKind",
]


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
