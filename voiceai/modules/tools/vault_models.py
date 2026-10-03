"""Credential/integration state rows for the tools vault (spec 0056, Phase-2).

Mongo-only documents behind the `Collections` registry; every model inherits
`database.base.BaseFields` (soft deletes only — no row is ever destroyed).
`PartnerCredential`/`PartnerIntegration` rows are tenant-scoped (the container
composes scoped views; `None` is never a query target). `IntegrationTemplate`
rows are system-curated (`tenant_id="system"`, the tools-catalog precedent)
with optional per-tenant overrides.

NOTE(spec-0056, Phase-2): the canonical home for these literals is
`voiceai/modules/tools/constants.py`, owned by the peer agent this phase —
the names here are defined locally to avoid a same-tree edit conflict, and the
integrator dedupes them into the module constants (keeping these re-exports).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.database.base import BaseFields
from voiceai.modules.tools.constants import (
    CREDENTIAL_TYPE_API_KEY,
    CREDENTIAL_TYPE_OAUTH2,
    INTEGRATION_STATUS_ACTIVE,
    CredentialType,
)

__all__ = [
    "CREDENTIAL_TYPE_API_KEY",
    "CREDENTIAL_TYPE_OAUTH2",
    "CredentialType",
    "INTEGRATION_STATUS_ACTIVE",
    "IntegrationTemplate",
    "PartnerCredential",
    "PartnerIntegration",
]

#: Credential shapes the vault stores (spec 0056 data model).


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
        credential_type: `oauth2` or `api_key`.
        encrypted_data: Vault envelope (opaque ciphertext — never logged).
        key_version: Key generation that sealed the envelope (`>= 1`).
        expires_at: Token expiry, or `None` for non-expiring keys.
        tenant_id: Owner tenant, stamped on write; `None` never queried.
    """

    provider: str = Field(..., min_length=1)
    credential_type: CredentialType = CREDENTIAL_TYPE_API_KEY
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
        tools_template: Curated tool shapes the provider offers.
        oauth_config: Discovery/registration options for the Phase-3 DCR flow.
        client_config: Default MCP client options (timeouts, versions).
        tenant_id: `system` for curated rows, a tenant id for overrides.
    """

    provider: str = Field(..., min_length=1)
    credential_type: CredentialType = CREDENTIAL_TYPE_API_KEY
    tools_template: list[dict[str, Any]] = Field(default_factory=list)  # why: curated tool shapes are free-form JSON
    oauth_config: dict = Field(default_factory=dict)  # why: DCR discovery options are free-form JSON
    client_config: dict = Field(default_factory=dict)  # why: client defaults are free-form JSON
    tenant_id: str | None = SYSTEM_TENANT_ID
