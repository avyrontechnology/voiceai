"""Vault-state orchestration: templates, credentials, links, DCR, policies (specs 0058, 0081).

`IntegrationService` is the tools module's second service (the registry keeps
`ToolsService`): everything the Integrations UI needs that is not the tool
registry itself. Reads merge system + tenant template views (tenant wins);
credentials and links are tenant-only. Secrets cross this file exactly twice:
inbound on connect (encrypted immediately) and outbound once in the same
call (`secret_once`); stored rows, logs, and list projections never carry
plaintext — `helpers.wire_credential_meta` is the only serializer, and it
cannot see `encrypted_data` by construction.

Owner scope (spec 0081, P2): every method takes an explicit `OwnerScope`
built by the controller from auth context (never client fields). The binding
is explicit everywhere (connect, DCR, OAuth authorize); the org policy enum
admits no invalid states; resolution is deterministic (`user_private` beats
`org_shared`, `enforce_shared` wins over ownership); every decision appends
an audit event (fail-open-tolerant — the decision stands regardless).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Final

from pydantic import ValidationError

from voiceai.common.errors import NotFoundError as _StoreNotFoundError
from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.audit_repository import AuditRepository
from voiceai.modules.tools.dcr_service import DcrService
from voiceai.modules.tools.errors import (
    InvalidToolError,
    ToolForbiddenError,
    ToolNotFoundError,
)
from voiceai.modules.tools.exceptions import (
    ensure_binding_permitted,
    ensure_binding_present,
    ensure_effective_allowed,
    ensure_owner_scope,
)
from voiceai.modules.tools.helpers import validation_field_paths
from voiceai.modules.tools.models import (
    IntegrationAuditLog,
    IntegrationTemplate,
    OrgPolicy,
    OwnerScope,
    PartnerCredential,
    PartnerIntegration,
    ToolDefinition,
)
from voiceai.modules.tools.org_policy_repository import OrgPolicyRepository
from voiceai.modules.tools.repository import ToolsRepository, VaultRepository
from voiceai.modules.tools.static_methods import build_tool_id, effective_binding_set
from voiceai.modules.tools.vault import CredentialVault

__all__ = ["IntegrationService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)

#: `auth_config` keys that would smuggle secret material into the NON-secret
#: placement mapping (spec 0059) — refused at the boundary, never stored.
_SEALED_LOOKING_KEYS: Final[frozenset[str]] = frozenset({"password", "token", "secret", "api_key", "headers"})
#: Template entry keys the sync understands (spec 0059 builder contract).
_SYNC_ENTRY_NAME: Final[str] = "name"
_SYNC_ENTRY_PATH: Final[str] = "path"
_SYNC_ENTRY_METHOD: Final[str] = "method"
_SYNC_METHODS: Final[frozenset[str]] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_SYNCED_FROM_KEY: Final[str] = "synced_from"


def _seal_material(credential_type: str, secret: str, provider: str) -> bytes:
    """Wrap secret text into the sealed JSON material for one auth type.

    Args:
        credential_type: The validated auth type.
        secret: Secret text (custom: JSON object text of static headers).
        provider: Partner name (identifiers only, for the 400 details).

    Returns:
        UTF-8 JSON bytes for the vault envelope.

    Raises:
        InvalidToolError: When custom JSON is not an object of strings.
    """
    if credential_type == C.CREDENTIAL_TYPE_API_KEY:
        material: dict[str, Any] = {"api_key": secret}
    elif credential_type in (C.CREDENTIAL_TYPE_BEARER, C.CREDENTIAL_TYPE_OAUTH2):
        material = {"token": secret}
    elif credential_type == C.CREDENTIAL_TYPE_BASIC:
        material = {"password": secret}
    elif credential_type == C.CREDENTIAL_TYPE_CUSTOM:
        try:
            parsed = json.loads(secret) if secret.strip() else {}
        except ValueError as exc:
            raise InvalidToolError(
                "Custom auth secret must be a JSON object of headers.",
                details={"provider": provider},
                cause=exc,
            ) from exc
        if not isinstance(parsed, dict) or not all(isinstance(value, str) for value in parsed.values()):
            raise InvalidToolError(
                "Custom auth secret must be a JSON object of headers.",
                details={"provider": provider},
            )
        material = {"headers": parsed}
    else:
        material = {}
    return json.dumps(material).encode("utf-8")


#: Starting key generation for freshly sealed envelopes (rotation bumps it).
_INITIAL_KEY_VERSION: Final[int] = 1


class IntegrationService:
    """Serve integration state over system + tenant vault views.

    Args:
        system: Vault views bound to the system tenant (curated templates).
        tenant: Vault views bound to the ambient request tenant.
        vault: Envelope encryption for inbound secrets (fails closed when dark).
        dcr: Outbound DCR client (stateless; `None` keeps register dark with a
            loud warning, mirroring the tools `agent_links=None` posture).
        tools: Tenant tool registry view receiving synced rows (spec 0059).
        policies: Org-policy views bound to the ambient request tenant
            (`None` keeps policy reads at the user_choice default — P2
            wiring always provides it; the port in core leaves it dark).
        audit: Governance-audit views bound to the ambient request tenant
            (`None` skips appends with a debug line — decisions still
            execute; the audit store itself stays fail-open).
    """

    def __init__(
        self,
        system: VaultRepository,
        tenant: VaultRepository,
        vault: CredentialVault,
        dcr: DcrService | None = None,
        *,
        tools: ToolsRepository | None = None,
        policies: OrgPolicyRepository | None = None,
        audit: AuditRepository | None = None,
    ) -> None:
        self._system = system
        self._tenant = tenant
        self._vault = vault
        self._dcr = dcr
        self._tools = tools
        self._policies = policies
        self._audit = audit

    async def _audit_event(
        self, *, action: str, target_kind: str, target_id: str, scope: OwnerScope, reason: str = ""
    ) -> None:
        """Append one governance event, or skip when unwired (spec 0081, P2).

        Args:
            action: Closed audit vocabulary (link/unlink joined with a P2 note).
            target_kind: What was decided on (template, credential, link, org_policy).
            target_id: The governed row id (identifiers only).
            scope: The acting scope (mapped to plain actor strings here).
            reason: Why (required for suspend / flip / promote decisions).
        """
        if self._audit is None:
            logger.debug("audit unwired: skipping %s %s:%s", action, target_kind, target_id)
            return
        await self._audit.append(
            IntegrationAuditLog(
                actor={
                    C.LOG_KEY_PARTNER: scope.partner_id,
                    C.LOG_KEY_ORG: scope.org_id or "",
                    C.LOG_KEY_USER: scope.user_id or "",
                },
                action=action,
                target_kind=target_kind,
                target_id=target_id,
                reason=reason,
            )
        )

    async def _policy_for(self, scope: OwnerScope) -> str:
        """Return the binding policy governing one scope (spec 0081, P2).

        Partner-wide scopes (`org_id=None`) and orgs without a stored row
        both read as `user_choice` — the neutral semantics (every connect
        explicit, resolution prefers private). No hard-coded default beyond
        this documented neutral.

        Args:
            scope: Who asks.

        Returns:
            The org's `binding_policy`, or `user_choice` when none governs.
        """
        if self._policies is None or scope.org_id is None:
            return C.POLICY_USER_CHOICE
        row = await self._policies.get_policy(scope.org_id, scope)
        if row is None:
            return C.POLICY_USER_CHOICE
        return row.binding_policy

    async def _replace_same_quad(self, *, provider: str, binding: str, scope: OwnerScope) -> PartnerCredential | None:
        """Find the live row with the same natural key for idempotent replace.

        The natural key is (provider, binding, org, user): a repeat connect
        reuses the id (replaced=True) while a different binding/org/user
        mints a new row (never clobbered).

        Args:
            provider: Partner name.
            binding: The requested binding (already validated).
            scope: Who connects.

        Returns:
            The same-quad row, or `None` when this connect mints fresh.
        """
        for row in await self._tenant.list_credentials_for_scope(scope, for_execution=False):
            if row.provider != provider or row.binding != binding or row.owner_org_id != scope.org_id:
                continue
            if binding == C.BINDING_USER_PRIVATE and row.owner_user_id != scope.user_id:
                continue
            if binding == C.BINDING_ORG_SHARED and row.owner_user_id is not None:
                continue
            return row
        return None

    async def list_templates(self, scope: OwnerScope) -> list[IntegrationTemplate]:
        """List curated + own-partner templates visible in a scope (spec 0081, P2).

        Global rows answer every scope; partner rows answer their owner only
        (each view filters its own rows; tenant wins id ties). Cross-partner
        rows never surface (no oracle).

        Args:
            scope: Who asks (partner boundary only — orgs share templates).

        Returns:
            Merged rows, system first (the tools-registry precedent).
        """
        ensure_owner_scope(scope)
        merged: dict[str, IntegrationTemplate] = {}
        system_ids: set[str] = set()
        for row in await self._system.list_templates_for_scope(scope):
            merged[row.provider] = row
            system_ids.add(row.provider)
        for row in await self._tenant.list_templates_for_scope(scope):
            merged[row.provider] = row
        ordered = sorted(merged, key=lambda provider: (provider not in system_ids, provider))
        logger.debug("templates listed: partner=%s returned=%s", scope.partner_id, len(ordered))
        return [merged[key] for key in ordered]

    async def save_template(
        self,
        *,
        provider: str,
        credential_type: str,
        scope: OwnerScope,
        transport: str = "http",
        tools_template: list[dict[str, Any]] | None = None,
        oauth_config: dict[str, Any] | None = None,
        client_config: dict[str, Any] | None = None,
        allowed_bindings: list[str] | None = None,
        suggested_binding: str | None = None,
    ) -> tuple[IntegrationTemplate, bool]:
        """Create or replace this partner's private template (spec 0081, P2).

        Visibility is forced to `partner_private` with the caller as owner —
        the payload cannot ask for global (admin globals stay seed/P6).
        Version is 1 on create and preserved on replace (P3 owns versioning);
        lifecycle stays draft with empty provenance (P3 owns transitions).

        Args:
            provider: Partner name (the natural key).
            credential_type: `none|api_key|bearer|basic|oauth2|custom`.
            scope: Who writes (owner stamped from here, never the payload).
            transport: `http` (manual entries) or `mcp` (auto-discovery).
            tools_template: Curated tool shapes the provider offers.
            oauth_config: Discovery/registration options for DCR.
            client_config: Default MCP client options.
            allowed_bindings: Binding contract (`None` = most-restrictive
                default `[user_private]`).
            suggested_binding: Hint inside the contract, or `None`.

        Returns:
            `(row, replaced)`: the persisted row and whether one existed.

        Raises:
            InvalidToolError: When the credential shape, transport, or
                binding contract is unknown (HTTP 400, identifiers only).
        """
        ensure_owner_scope(scope)
        if credential_type not in (
            C.CREDENTIAL_TYPE_NONE,
            C.CREDENTIAL_TYPE_API_KEY,
            C.CREDENTIAL_TYPE_BEARER,
            C.CREDENTIAL_TYPE_BASIC,
            C.CREDENTIAL_TYPE_OAUTH2,
            C.CREDENTIAL_TYPE_CUSTOM,
        ):
            raise InvalidToolError(
                f"Unknown credential type {credential_type!r}.",
                details={"provider": provider},
            )
        if transport not in (C.TRANSPORT_HTTP, C.TRANSPORT_MCP):
            raise InvalidToolError(
                f"Unknown transport {transport!r}.",
                details={"provider": provider},
            )
        existing = await self._tenant.get_template_in_scope(provider, scope)
        bindings: dict[str, Any] = {}
        if allowed_bindings is not None:
            bindings["allowed_bindings"] = list(allowed_bindings)
        try:
            row = IntegrationTemplate(
                provider=provider,
                credential_type=credential_type,
                transport=transport,
                tools_template=[dict(entry) for entry in (tools_template or [])],
                oauth_config=dict(oauth_config or {}),
                client_config=dict(client_config or {}),
                visibility=C.VISIBILITY_PARTNER_PRIVATE,
                owner_partner_id=scope.partner_id,
                suggested_binding=suggested_binding,
                **bindings,
            )
        except ValidationError as exc:
            raise InvalidToolError(
                "Template binding contract is invalid.",
                details={"provider": provider, C.DETAIL_KEY_FIELDS: validation_field_paths(exc)},
                cause=exc,
            ) from exc
        if existing is not None:
            row.version = existing.version  # P3 owns versioning — P2 preserves it on replace
        saved = await self._tenant.save_template(row)
        logger.info("integration template saved for provider")
        return saved, existing is not None

    async def delete_template(self, provider: str, scope: OwnerScope) -> None:
        """Soft-delete this partner's template override; system rows are 403.

        Args:
            provider: Partner name (the natural key).
            scope: Who writes.

        Raises:
            ToolForbiddenError: When a curated system row exists (HTTP 403).
            ToolNotFoundError: When no visible row exists (HTTP 404, no oracle).
        """
        from voiceai.modules.tools.exceptions import ensure_system_immutable

        ensure_owner_scope(scope)
        ensure_system_immutable(await self._system.get_template_in_scope(provider, scope) is not None, provider)
        try:
            deleted = await self._tenant.delete_template(provider)
        except _StoreNotFoundError:
            deleted = False
        if not deleted:
            raise ToolNotFoundError(
                f"Template {provider!r} not found.",
                details={"provider": provider},
            )
        await self._audit_event(
            action=C.AUDIT_ACTION_RETIRE, target_kind=C.AUDIT_TARGET_TEMPLATE, target_id=provider, scope=scope
        )

    async def list_credentials(self, scope: OwnerScope) -> list[PartnerCredential]:
        """List credential rows visible in a scope (spec 0081, P2).

        Manage-path projection (org admins see member metadata — secrets never
        ride projections); callers MUST serialize through
        `helpers.wire_credential_meta`.

        Args:
            scope: Who asks.

        Returns:
            Visible rows, oldest first.
        """
        ensure_owner_scope(scope)
        rows = list(await self._tenant.list_credentials_for_scope(scope, for_execution=False))
        logger.debug("credentials listed: partner=%s org=%s returned=%s", scope.partner_id, scope.org_id, len(rows))
        return rows

    async def resolve_credential(self, *, provider: str, scope: OwnerScope) -> PartnerCredential | None:
        """Resolve the live credential for a provider in a scope (spec 0081, P2).

        Deterministic: org policy first (absent row = neutral `user_choice`),
        then the strict execution-path read (`user_private` beats `org_shared`
        by ownership; `enforce_shared` wins over ownership), then the status
        gate (suspended/revoked never resolve; expired still resolves —
        refresh is on-demand). Pure read: no writes, no audit. P5 calls this
        blind from the gateway.

        Args:
            provider: Partner name.
            scope: Who the run acts for.

        Returns:
            The live row, or `None` when nothing may ride this call.
        """
        ensure_owner_scope(scope)
        rows = await self._tenant.find_credentials_for_scope(provider, scope, for_execution=True)
        own_private = [
            row for row in rows if row.binding == C.BINDING_USER_PRIVATE and row.owner_user_id == scope.user_id
        ]
        shared = [row for row in rows if row.binding == C.BINDING_ORG_SHARED]
        live = next(
            (
                row
                for row in (*own_private, *shared)
                if row.status not in (C.CREDENTIAL_STATUS_SUSPENDED, C.CREDENTIAL_STATUS_REVOKED)
            ),
            None,
        )
        logger.debug(
            C.LOG_CREDENTIAL_RESOLVED,
            provider,
            live.binding if live else None,
            scope.org_id,
        )
        return live

    async def connect_credential(
        self,
        *,
        provider: str,
        credential_type: str,
        binding: str | None,
        scope: OwnerScope,
        secret: str,
        auth_config: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[PartnerCredential, bool]:
        """Seal a secret and store (or replace) one scoped row (spec 0081, P2).

        Explicit binding everywhere: omitted bindings answer 422, policy
        refusals 403, empty effective sets 422 (§35.4). The natural key is
        (provider, binding, org, user) — a repeat connect reuses the id
        (`replaced=True`, reconnect also restores suspended rows); a
        different quad mints a new row (never clobbered).

        Args:
            provider: Partner name (code/DB constant at call sites).
            credential_type: `none|api_key|bearer|basic|oauth2|custom`.
            binding: `org_shared|user_private` (required — `None` is 422).
            scope: Who connects (owner stamped from here, never the payload).
            secret: Secret text (custom: JSON object text of static headers).
            auth_config: NON-secret placement (secret-looking keys refused).
            expires_at: Token expiry, or `None` for non-expiring keys.

        Returns:
            `(row, replaced)`: the persisted row and whether one existed.

        Raises:
            BindingRequiredError: When the binding was omitted (HTTP 422).
            ToolForbiddenError: When the policy forbids the binding, or a
                private binding has no user (HTTP 403 / 400 respectively).
            PolicyConflictError: When template ∩ policy is empty (HTTP 422).
            InvalidToolError: When the shape is unknown or `auth_config`
                smuggles secret-looking keys (HTTP 400).
        """
        ensure_owner_scope(scope)
        binding = ensure_binding_present(binding, provider)
        policy = await self._policy_for(scope)
        ensure_binding_permitted(binding, policy, provider)
        if binding == C.BINDING_USER_PRIVATE and not scope.user_id:
            raise InvalidToolError(
                "Private credentials need a user login — API keys cannot hold them.",
                details={"provider": provider},
            )
        template = await self._system.get_template_in_scope(provider, scope)
        if template is None:
            template = await self._tenant.get_template_in_scope(provider, scope)
        if template is not None:
            ensure_effective_allowed(list(template.allowed_bindings), policy, provider)
        if credential_type not in (
            C.CREDENTIAL_TYPE_NONE,
            C.CREDENTIAL_TYPE_API_KEY,
            C.CREDENTIAL_TYPE_BEARER,
            C.CREDENTIAL_TYPE_BASIC,
            C.CREDENTIAL_TYPE_OAUTH2,
            C.CREDENTIAL_TYPE_CUSTOM,
        ):
            raise InvalidToolError(
                f"Unknown credential type {credential_type!r}.",
                details={"provider": provider},
            )
        config = dict(auth_config or {})
        smuggled = sorted(set(config) & _SEALED_LOOKING_KEYS)
        if smuggled:
            raise InvalidToolError(
                "auth_config carries secret material — seal it as the secret instead.",
                details={"provider": provider},
            )
        envelope = self._vault.encrypt(
            _seal_material(credential_type, secret, provider), key_version=_INITIAL_KEY_VERSION
        )
        same = await self._replace_same_quad(provider=provider, binding=binding, scope=scope)
        replaced = same is not None
        row = PartnerCredential(
            provider=provider,
            credential_type=credential_type,
            auth_config=config,
            encrypted_data=envelope,
            key_version=_INITIAL_KEY_VERSION,
            expires_at=expires_at,
            owner_org_id=scope.org_id,
            owner_user_id=scope.user_id if binding == C.BINDING_USER_PRIVATE else None,
            binding=binding,  # type: ignore[arg-type]  # why: presence + policy checks above narrow it
            status=C.CREDENTIAL_STATUS_ACTIVE,
        )
        if same is not None and same.id is not None:
            row.id = same.id
        saved = await self._tenant.save_credential(row)
        await self._audit_event(
            action=C.AUDIT_ACTION_CONNECT,
            target_kind=C.AUDIT_TARGET_CREDENTIAL,
            target_id=saved.id or "",
            scope=scope,
        )
        logger.info(
            "credential connected: provider=%s binding=%s org=%s replaced=%s",
            provider,
            binding,
            scope.org_id,
            replaced,
        )
        return saved, replaced

    async def revoke_credential(self, credential_id: str, scope: OwnerScope) -> None:
        """Soft-delete one credential row; foreign rows read as missing.

        Revoke takes the manage path (org admins can revoke member rows —
        operability wins; every revoke is an audit event with the actor).

        Args:
            credential_id: Storage id.
            scope: Who revokes.

        Raises:
            ToolNotFoundError: When no visible row exists (HTTP 404, no oracle).
        """
        ensure_owner_scope(scope)
        row = await self._tenant.get_credential_in_scope(credential_id, scope, for_execution=False)
        if row is None:
            raise ToolNotFoundError(
                f"Credential {credential_id!r} not found.",
                details={"credential_id": credential_id},
            )
        try:
            deleted = await self._tenant.delete_credential(credential_id)
        except _StoreNotFoundError:
            deleted = False
        if not deleted:
            raise ToolNotFoundError(
                f"Credential {credential_id!r} not found.",
                details={"credential_id": credential_id},
            )
        await self._audit_event(
            action=C.AUDIT_ACTION_REVOKE,
            target_kind=C.AUDIT_TARGET_CREDENTIAL,
            target_id=credential_id,
            scope=scope,
        )

    async def list_integrations(self, *, scope: OwnerScope, agent_id: str | None = None) -> list[PartnerIntegration]:
        """List link rows visible in a scope, optionally for one agent (spec 0081, P2).

        Args:
            scope: Who asks (`org_id=None` means partner-wide manage).
            agent_id: Narrow to one agent's links, or all.

        Returns:
            Visible rows, oldest first.
        """
        ensure_owner_scope(scope)
        rows = list(await self._tenant.list_integrations_for_scope(scope, agent_id=agent_id))
        logger.debug("integrations listed: partner=%s org=%s returned=%s", scope.partner_id, scope.org_id, len(rows))
        return rows

    async def link_integration(
        self, *, provider: str, agent_id: str, config: dict[str, Any], scope: OwnerScope
    ) -> PartnerIntegration:
        """Link a provider to an agent; requires a live credential (spec 0081, P2).

        The org comes from the scope (never the payload — client identity
        fields are untrusted); the credential must resolve on the strict
        execution path under the effective policy.

        Args:
            provider: Partner name (must resolve live in this scope).
            agent_id: Agent this link belongs to.
            config: Non-secret runtime options (secrets stay in credentials).
            scope: Who links (org stamped from here; org-less scopes 400).

        Returns:
            The persisted link row.

        Raises:
            InvalidToolError: When the scope carries no org, or no live
                credential resolves (HTTP 400, identifiers only).
        """
        ensure_owner_scope(scope)
        if scope.org_id is None:
            raise InvalidToolError(
                "Links belong to an org — connect from inside one.",
                details={"provider": provider},
            )
        live = await self.resolve_credential(provider=provider, scope=scope)
        if live is None:
            raise InvalidToolError(
                f"Provider {provider!r} has no connected credential.",
                details={"provider": provider},
            )
        saved = await self._tenant.save_integration(
            PartnerIntegration(provider=provider, agent_id=agent_id, config=dict(config), org_id=scope.org_id)
        )
        await self._audit_event(
            action=C.AUDIT_ACTION_LINK,
            target_kind=C.AUDIT_TARGET_LINK,
            target_id=saved.id or "",
            scope=scope,
        )
        logger.info("integration linked: provider=%s agent=%s org=%s", provider, agent_id, scope.org_id)
        return saved

    async def unlink_integration(self, integration_id: str, scope: OwnerScope) -> None:
        """Soft-delete one link row; foreign rows read as missing (spec 0081, P2).

        Args:
            integration_id: Storage id.
            scope: Who unlinks (manage-path visibility).

        Raises:
            ToolNotFoundError: When no visible row exists (HTTP 404, no oracle).
        """
        ensure_owner_scope(scope)
        rows = await self._tenant.list_integrations_for_scope(scope)
        row = next((item for item in rows if item.id == integration_id), None)
        if row is None:
            raise ToolNotFoundError(
                f"Integration {integration_id!r} not found.",
                details={"integration_id": integration_id},
            )
        try:
            deleted = await self._tenant.delete_integration(integration_id)
        except _StoreNotFoundError:
            deleted = False
        if not deleted:
            raise ToolNotFoundError(
                f"Integration {integration_id!r} not found.",
                details={"integration_id": integration_id},
            )
        await self._audit_event(
            action=C.AUDIT_ACTION_UNLINK,
            target_kind=C.AUDIT_TARGET_LINK,
            target_id=integration_id,
            scope=scope,
        )

    async def get_policy(self, org_id: str, scope: OwnerScope) -> OrgPolicy:
        """Return one org's policy row (spec 0081, P2).

        Args:
            org_id: Home org.
            scope: Who asks (org match or partner-wide manage).

        Returns:
            The stored row.

        Raises:
            ToolNotFoundError: When no row exists or the scope may not see
                it (HTTP 404, no oracle).
        """
        ensure_owner_scope(scope)
        if self._policies is None:
            raise ToolNotFoundError(f"Policy {org_id!r} not found.", details={"org_id": org_id})
        row = await self._policies.get_policy(org_id, scope)
        if row is None:
            raise ToolNotFoundError(f"Policy {org_id!r} not found.", details={"org_id": org_id})
        return row

    async def set_policy(
        self, *, org_id: str, binding_policy: str, changed_by: str, reason: str, scope: OwnerScope
    ) -> tuple[OrgPolicy, list[str]]:
        """Write one org's policy, suspending barred rows on the way in (spec 0081, P2).

        Strict org match (no admin bypass in P2). Turning `enforce_shared`
        ON suspends (never deletes) the org's live `user_private` rows so
        rollback stays possible; turning it OFF never auto-restores
        (explicit reconnect required). Order is suspend-rows → save-policy →
        audit, so a crash never leaves policy ahead of rows.

        Args:
            org_id: Home org (must equal the caller's org).
            binding_policy: `user_choice|shared_with_override|enforce_shared`.
            changed_by: Actor principal (identifiers only, for the ledger).
            reason: Why (required on flips — enforced by callers' copy).
            scope: Who writes.

        Returns:
            `(row, suspended)`: the saved policy and the suspended row ids.

        Raises:
            ToolForbiddenError: When writing another org's policy (HTTP 403).
            InvalidToolError: When the policy literal is unknown (HTTP 400)
                or policies are unwired in this composition.
        """
        ensure_owner_scope(scope)
        if self._policies is None:
            raise InvalidToolError(
                "Org policies are not wired in this deployment.",
                details={"org_id": org_id},
            )
        if scope.org_id != org_id:
            raise ToolForbiddenError(
                f"Policy writes stay inside the caller's org.",
                details={"org_id": org_id},
            )
        if binding_policy not in (
            C.POLICY_USER_CHOICE,
            C.POLICY_SHARED_WITH_OVERRIDE,
            C.POLICY_ENFORCE_SHARED,
        ):
            raise InvalidToolError(
                f"Unknown binding policy {binding_policy!r}.",
                details={"org_id": org_id},
            )
        suspended: list[str] = []
        old_row = await self._policies.get_policy(org_id, scope)
        old_policy = old_row.binding_policy if old_row is not None else None
        if binding_policy == C.POLICY_ENFORCE_SHARED:
            for row in await self._tenant.list_credentials_for_scope(scope, for_execution=False):
                if (
                    row.binding == C.BINDING_USER_PRIVATE
                    and row.owner_org_id == org_id
                    and row.status == C.CREDENTIAL_STATUS_ACTIVE
                    and row.id is not None
                ):
                    row.status = C.CREDENTIAL_STATUS_SUSPENDED
                    await self._tenant.save_credential(row)
                    suspended.append(row.id)
                    await self._audit_event(
                        action=C.AUDIT_ACTION_SUSPEND,
                        target_kind=C.AUDIT_TARGET_CREDENTIAL,
                        target_id=row.id,
                        scope=scope,
                        reason=reason,
                    )
                    logger.info(C.LOG_CREDENTIAL_SUSPENDED, row.id, row.provider, reason)
        saved = await self._policies.save_policy(
            OrgPolicy(org_id=org_id, binding_policy=binding_policy, changed_by=changed_by, reason=reason),
            scope,
        )
        await self._audit_event(
            action=C.AUDIT_ACTION_POLICY_FLIP,
            target_kind=C.AUDIT_TARGET_POLICY,
            target_id=org_id,
            scope=scope,
            reason=reason,
        )
        logger.info(C.LOG_POLICY_FLIP, org_id, old_policy, binding_policy, suspended)
        return saved, suspended

    async def dcr_register(
        self, *, issuer_url: str, provider: str, binding: str | None, scope: OwnerScope
    ) -> PartnerCredential:
        """Register at an issuer and seal the issued secret immediately (spec 0081, P2).

        The issued `client_secret` never leaves the server: it is sealed
        into the vault material at registration, and the caller only learns
        identifiers (`client_id`, endpoints). The row carries no access
        `token` yet (status `pending`) — the login step resolves the secret
        server-side from this row, so no human ever handles it.

        Args:
            issuer_url: Provider issuer URL (SSRF-checked inside DCR).
            provider: Partner name the credential is stored under.
            binding: `org_shared|user_private` (required — `None` is 422,
                checked against the policy intent before any outbound call).
            scope: Who registers (owner stamped from here).

        Returns:
            The persisted credential row (metadata only on the wire).

        Raises:
            BindingRequiredError: When the binding was omitted (HTTP 422).
            ToolForbiddenError: When the policy forbids the binding (HTTP 403).
            ToolExecutionError: When discovery fails or MANUAL is required.
            InvalidToolError: When DCR is unwired in this composition.
        """
        ensure_owner_scope(scope)
        binding = ensure_binding_present(binding, provider)
        ensure_binding_permitted(binding, provider=provider, policy=await self._policy_for(scope))
        if self._dcr is None:
            logger.warning("dcr register unwired: refusing outbound registration")
            raise InvalidToolError(
                "Dynamic registration is not wired in this deployment.",
                details={"issuer_url": issuer_url},
            )
        record = await self._dcr.register_client(issuer_url)
        auth_config: dict[str, Any] = {
            C.CLIENT_ID_KEY: record.client_id,
            C.ISSUER_URL_KEY: issuer_url,
        }
        if record.authorization_endpoint:
            auth_config["authorization_endpoint"] = record.authorization_endpoint
        if record.token_endpoint:
            auth_config["token_endpoint"] = record.token_endpoint
        material = {"client_secret": record.client_secret or ""}
        envelope = self._vault.encrypt(json.dumps(material).encode("utf-8"), key_version=_INITIAL_KEY_VERSION)
        same = await self._replace_same_quad(provider=provider, binding=binding, scope=scope)
        row = PartnerCredential(
            provider=provider,
            credential_type=C.CREDENTIAL_TYPE_OAUTH2,
            auth_config=auth_config,
            encrypted_data=envelope,
            key_version=_INITIAL_KEY_VERSION,
            owner_org_id=scope.org_id,
            owner_user_id=scope.user_id if binding == C.BINDING_USER_PRIVATE else None,
            binding=binding,  # type: ignore[arg-type]  # why: presence + policy checks above narrow it
            status=C.CREDENTIAL_STATUS_PENDING,
        )
        if same is not None and same.id is not None:
            row.id = same.id
        saved = await self._tenant.save_credential(row)
        await self._audit_event(
            action=C.AUDIT_ACTION_CONNECT,
            target_kind=C.AUDIT_TARGET_CREDENTIAL,
            target_id=saved.id or "",
            scope=scope,
        )
        logger.info("dcr client stored: provider=%s binding=%s org=%s", provider, binding, scope.org_id)
        return saved

    async def sync_tools(self, provider: str) -> dict[str, Any]:
        """Materialize a template's tools into registry rows (specs 0059/0060).

        `http` templates use the manual entries (`url = base_url + path`);
        `mcp` templates auto-discover from the remote server (`initialize` →
        `tools/list`) and ignore manual entries (reported as skipped).
        Re-sync updates ONLY rows this provider stamped before
        (`meta.synced_from`) — hand-made same-name rows are `skipped`, never
        clobbered. Entry problems collect; valid entries still sync.

        Args:
            provider: Partner name (merged system+tenant template lookup).

        Returns:
            `{created, updated, skipped, tool_ids, problems}` (problems are
            identifier-only strings, never secrets or URLs).

        Raises:
            ToolNotFoundError: When no template names the provider (404).
            InvalidToolError: When the sync seam is unwired, no live
                credential exists, the auth type cannot ride the transport,
                or the endpoint fails the safety pre-flight (400).
        """
        from voiceai.common.security import is_safe_outbound_url

        if self._tools is None:
            logger.warning("tools sync unwired: no registry view for provider")
            raise InvalidToolError(
                "Tool sync is not wired in this deployment.",
                details={"provider": provider},
            )
        template = await self._system.get_template(provider)
        if template is None:
            template = await self._tenant.get_template(provider)
        if template is None:
            raise ToolNotFoundError(
                f"Template {provider!r} not found.",
                details={"provider": provider},
            )
        transport = template.transport or C.TRANSPORT_HTTP
        if transport == C.TRANSPORT_MCP:
            return await self._sync_mcp_tools(template)
        base_url = ""
        if isinstance(template.client_config, dict):
            candidate = template.client_config.get("base_url", "")
            if isinstance(candidate, str):
                base_url = candidate.rstrip("/")
        if base_url:
            safe = await asyncio.to_thread(is_safe_outbound_url, base_url)
            if not safe:
                raise InvalidToolError(
                    "Template base URL failed safety validation.",
                    details={"provider": provider},
                )
        auth_ref = await self._sync_auth_ref(template)
        counters: dict[str, Any] = {"created": 0, "updated": 0, "skipped": [], "tool_ids": []}
        problems: list[str] = []
        entries = template.tools_template if isinstance(template.tools_template, list) else []
        for position, entry in enumerate(entries):
            where = f"tools_template[{position}]"
            if not isinstance(entry, dict):
                problems.append(f"{where}: entry must be an object")
                continue
            name = entry.get(_SYNC_ENTRY_NAME)
            if not isinstance(name, str) or not name or "/" in name or ":" in name or " " in name:
                problems.append(f"{where}: bad tool name")
                continue
            method = entry.get(_SYNC_ENTRY_METHOD, C.DEFAULT_TOOL_METHOD)
            if not isinstance(method, str) or method.upper() not in _SYNC_METHODS:
                problems.append(f"{where}: bad method for {name!r}")
                continue
            parameters = entry.get("parameters", {})
            if not isinstance(parameters, dict):
                problems.append(f"{where}: parameters must be an object for {name!r}")
                continue
            path = entry.get(_SYNC_ENTRY_PATH, "")
            if path and (not isinstance(path, str) or not path.startswith("/")):
                problems.append(f"{where}: path must start with / for {name!r}")
                continue
            url = f"{base_url}{path}" if path else None
            if path and not base_url:
                problems.append(f"{where}: path without base_url for {name!r}")
                continue
            description = entry.get("description", "")
            tool_id = build_tool_id(C.TOOL_KIND_FUNCTION, name)
            await self._store_synced_row(
                provider=provider,
                tool_id=tool_id,
                name=name,
                description=description if isinstance(description, str) else "",
                parameters=parameters,
                url=url,
                method=method.upper(),
                auth_ref=auth_ref,
                extra_meta={"transport": C.TRANSPORT_HTTP},
                counters=counters,
            )
        logger.info(
            "tools synced for provider: %d created, %d updated, %d skipped",
            counters["created"],
            counters["updated"],
            len(counters["skipped"]),
        )
        return {
            "created": counters["created"],
            "updated": counters["updated"],
            "skipped": counters["skipped"],
            "tool_ids": counters["tool_ids"],
            "problems": problems,
        }

    async def _sync_auth_ref(self, template: IntegrationTemplate) -> str | None:
        """Resolve the live credential pointer for one template (spec 0059).

        Args:
            template: The merged template row.

        Returns:
            `cred:<id>`, or `None` for `none` types.

        Raises:
            InvalidToolError: When a non-`none` type has no live credential.
        """
        if template.credential_type == C.CREDENTIAL_TYPE_NONE:
            return None
        live = await self._tenant.find_credentials_by_provider(template.provider)
        if not live or live[0].id is None:
            raise InvalidToolError(
                f"Provider {template.provider!r} has no connected credential.",
                details={"provider": template.provider},
            )
        return f"{C.AUTH_REF_PREFIX}{live[0].id}"

    async def _store_synced_row(
        self,
        *,
        provider: str,
        tool_id: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        url: str | None,
        method: str,
        auth_ref: str | None,
        extra_meta: dict[str, str],
        counters: dict[str, Any],
    ) -> None:
        """Upsert one synced row honoring the ownership rule (spec 0059).

        Rows stamped by another provider (or hand-made) are `skipped`, never
        clobbered; own rows update in place.

        Args:
            provider: Partner name (the sync owner).
            tool_id: Natural key (`function:{name}`).
            name: Human label.
            description: What the tool does.
            parameters: JSON-Schema contract.
            url: Endpoint (http) or server URL (mcp).
            method: HTTP method (http rows; mcp rows pin POST).
            auth_ref: Credential pointer (or `None`).
            extra_meta: Extra stamps beside `synced_from`.
            counters: Mutable `{created, updated, skipped, tool_ids}` tallies.
        """
        if self._tools is None:
            raise InvalidToolError("Tool sync called before wiring check.")
        existing = await self._tools.get_tool(tool_id)
        if existing is not None and existing.meta.get(_SYNCED_FROM_KEY) != provider:
            counters["skipped"].append(tool_id)
            return
        row = ToolDefinition(
            tool_id=tool_id,
            kind=C.TOOL_KIND_FUNCTION,
            name=name,
            description=description,
            parameters=dict(parameters),
            url=url,
            method=method,
            auth_ref=auth_ref,
            timeout_s=existing.timeout_s if existing else C.TOOL_TIMEOUT_S,
            deprecated=False,
        )
        row.meta[_SYNCED_FROM_KEY] = provider
        for key, value in extra_meta.items():
            row.meta[key] = value
        await self._tools.save_tool(row)
        counters["tool_ids"].append(tool_id)
        if existing is None:
            counters["created"] += 1
        else:
            counters["updated"] += 1

    async def _sync_mcp_tools(self, template: IntegrationTemplate) -> dict[str, Any]:
        """Discover a remote MCP server's tools and materialize rows (spec 0060).

        Manual entries are ignored (reported as skipped with reason) — the
        server is the source of truth. Discovery is one authenticated
        `initialize` → bounded `tools/list`; only bearer/oauth2-token and
        custom-header credentials can ride it.

        Args:
            template: The merged template row (`transport == "mcp"`).

        Returns:
            `{created, updated, skipped, tool_ids, problems}`.

        Raises:
            InvalidToolError: When the endpoint is missing/unsafe or the
                credential type cannot ride MCP (400, identifiers only).
        """
        from voiceai.common.security import is_safe_outbound_url
        from voiceai.modules.tools.adapters.mcp.client import McpHttpClient

        provider = template.provider
        endpoint = ""
        if isinstance(template.client_config, dict):
            candidate = template.client_config.get("base_url", "")
            if isinstance(candidate, str):
                endpoint = candidate.rstrip("/")
        if not endpoint:
            raise InvalidToolError(
                "MCP templates need a server URL in client_config.base_url.",
                details={"provider": provider},
            )
        safe = await asyncio.to_thread(is_safe_outbound_url, endpoint)
        if not safe:
            raise InvalidToolError(
                "Template server URL failed safety validation.",
                details={"provider": provider},
            )
        access_token: str | None = None
        extra_headers: dict[str, str] = {}
        if template.credential_type != C.CREDENTIAL_TYPE_NONE:
            live = await self._tenant.find_credentials_by_provider(provider)
            if not live:
                raise InvalidToolError(
                    f"Provider {provider!r} has no connected credential.",
                    details={"provider": provider},
                )
            try:
                plaintext = self._vault.decrypt(live[0].encrypted_data, key_version=live[0].key_version)
                material = json.loads(plaintext.decode("utf-8"))
            except Exception as exc:
                raise InvalidToolError(
                    "Stored credential cannot be opened for discovery.",
                    details={"provider": provider},
                ) from exc
            if not isinstance(material, dict):
                raise InvalidToolError(
                    "Stored credential cannot be opened for discovery.",
                    details={"provider": provider},
                )
            if template.credential_type in (C.CREDENTIAL_TYPE_BEARER, C.CREDENTIAL_TYPE_OAUTH2):
                token = material.get("token")
                if not isinstance(token, str) or not token:
                    raise InvalidToolError(
                        "Stored credential carries no token for discovery.",
                        details={"provider": provider},
                    )
                access_token = token
            elif template.credential_type == C.CREDENTIAL_TYPE_CUSTOM:
                headers = material.get("headers")
                if not isinstance(headers, dict) or not headers:
                    raise InvalidToolError(
                        "Stored credential carries no headers for discovery.",
                        details={"provider": provider},
                    )
                extra_headers = {str(key): value for key, value in headers.items() if isinstance(value, str)}
            else:
                raise InvalidToolError(
                    f"Credential type {template.credential_type!r} cannot ride MCP "
                    "discovery — use bearer, oauth2, or custom.",
                    details={"provider": provider},
                )
        try:
            client = McpHttpClient(endpoint, access_token, timeout_s=30.0, extra_headers=extra_headers)
            await client.initialize(C.MCP_PROTOCOL_VERSION)
            discovered = await client.list_all_tools()
        except Exception as exc:
            raise InvalidToolError(
                "MCP discovery failed for the server.",
                details={"provider": provider},
            ) from exc
        if self._tools is None:
            raise InvalidToolError("MCP discovery called before wiring check.")
        auth_ref = await self._sync_auth_ref(template)
        counters: dict[str, Any] = {"created": 0, "updated": 0, "skipped": [], "tool_ids": []}
        problems: list[str] = []
        manual = template.tools_template if isinstance(template.tools_template, list) else []
        for position in range(len(manual)):
            problems.append(f"tools_template[{position}]: ignored — MCP discovery owns this template")
        for remote in discovered:
            if not isinstance(remote, dict):
                continue
            name = remote.get("name")
            if not isinstance(name, str) or not name or "/" in name or ":" in name or " " in name:
                problems.append("discovered tool with a bad name (skipped)")
                continue
            schema = remote.get("inputSchema", {})
            await self._store_synced_row(
                provider=provider,
                tool_id=build_tool_id(C.TOOL_KIND_FUNCTION, name),
                name=name,
                description=remote.get("description", "") if isinstance(remote.get("description"), str) else "",
                parameters=schema if isinstance(schema, dict) else {},
                url=endpoint,
                method=C.DEFAULT_TOOL_METHOD,
                auth_ref=auth_ref,
                extra_meta={"transport": C.TRANSPORT_MCP, "mcp_tool": name},
                counters=counters,
            )
        logger.info(
            "mcp tools synced for provider: %d created, %d updated, %d skipped",
            counters["created"],
            counters["updated"],
            len(counters["skipped"]),
        )
        return {
            "created": counters["created"],
            "updated": counters["updated"],
            "skipped": counters["skipped"],
            "tool_ids": counters["tool_ids"],
            "problems": problems,
        }
