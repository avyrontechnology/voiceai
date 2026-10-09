"""Vault-state orchestration: templates, credentials, links, DCR (spec 0058).

`IntegrationService` is the tools module's second service (the registry keeps
`ToolsService`): everything the Integrations UI needs that is not the tool
registry itself. Reads merge system + tenant template views (tenant wins);
credentials and links are tenant-only. Secrets cross this file exactly twice:
inbound on connect (encrypted immediately) and outbound once in the same
call (`secret_once`); stored rows, logs, and list projections never carry
plaintext — `helpers.wire_credential_meta` is the only serializer, and it
cannot see `encrypted_data` by construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Final

from voiceai.common.errors import NotFoundError as _StoreNotFoundError
from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.dcr_service import DcrService
from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.static_methods import build_tool_id
from voiceai.modules.tools.vault import CredentialVault
from voiceai.modules.tools.vault_models import IntegrationTemplate, PartnerCredential, PartnerIntegration
from voiceai.modules.tools.vault_repository import VaultRepository

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
    """

    def __init__(
        self,
        system: VaultRepository,
        tenant: VaultRepository,
        vault: CredentialVault,
        dcr: DcrService | None = None,
        *,
        tools: ToolsRepository | None = None,
    ) -> None:
        self._system = system
        self._tenant = tenant
        self._vault = vault
        self._dcr = dcr
        self._tools = tools

    async def list_templates(self) -> list[IntegrationTemplate]:
        """List curated + own-tenant templates, tenant rows winning ties.

        Returns:
            Merged rows, system first (the tools-registry precedent).
        """
        merged: dict[str, IntegrationTemplate] = {}
        system_ids: set[str] = set()
        for row in await self._system.list_templates():
            merged[row.provider] = row
            system_ids.add(row.provider)
        for row in await self._tenant.list_templates():
            merged[row.provider] = row
        ordered = sorted(merged, key=lambda provider: (provider not in system_ids, provider))
        return [merged[key] for key in ordered]

    async def save_template(
        self,
        *,
        provider: str,
        credential_type: str,
        transport: str = "http",
        tools_template: list[dict[str, Any]] | None = None,
        oauth_config: dict[str, Any] | None = None,
        client_config: dict[str, Any] | None = None,
    ) -> tuple[IntegrationTemplate, bool]:
        """Create or replace this tenant's template for a provider.

        Tenant rows override curated system rows on read; system rows are
        never touched here (curation happens in code review + seed).

        Args:
            provider: Partner name (the natural key).
            credential_type: `none|api_key|bearer|basic|oauth2|custom`.
            transport: `http` (manual entries) or `mcp` (auto-discovery).
            tools_template: Curated tool shapes the provider offers.
            oauth_config: Discovery/registration options for DCR.
            client_config: Default MCP client options.

        Returns:
            `(row, replaced)`: the persisted row and whether one existed.

        Raises:
            InvalidToolError: When the credential shape or transport is
                unknown (HTTP 400).
        """
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
        existing = await self._tenant.get_template(provider)
        row = IntegrationTemplate(
            provider=provider,
            credential_type=credential_type,
            transport=transport,
            tools_template=[dict(entry) for entry in (tools_template or [])],
            oauth_config=dict(oauth_config or {}),
            client_config=dict(client_config or {}),
        )
        saved = await self._tenant.save_template(row)
        logger.info("integration template saved for provider")
        return saved, existing is not None

    async def delete_template(self, provider: str) -> None:
        """Soft-delete this tenant's template override; system rows are 403.

        Args:
            provider: Partner name (the natural key).

        Raises:
            ToolForbiddenError: When a curated system row exists (HTTP 403).
            ToolNotFoundError: When no visible row exists (HTTP 404, no oracle).
        """
        from voiceai.modules.tools.exceptions import ensure_system_immutable

        ensure_system_immutable(await self._system.get_template(provider) is not None, provider)
        try:
            deleted = await self._tenant.delete_template(provider)
        except _StoreNotFoundError:
            deleted = False
        if not deleted:
            raise ToolNotFoundError(
                f"Template {provider!r} not found.",
                details={"provider": provider},
            )

    async def list_credentials(self) -> list[PartnerCredential]:
        """List own-tenant credential rows (ciphertext included — project on wire).

        Returns:
            Active rows, oldest first. Callers MUST serialize through
            `helpers.wire_credential_meta` (the envelope never leaves the store).
        """
        return list(await self._tenant.list_credentials())

    async def connect_credential(
        self,
        *,
        provider: str,
        credential_type: str,
        secret: str,
        auth_config: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[PartnerCredential, bool]:
        """Seal a secret and store (or replace) this tenant's row for a provider.

        Idempotent by natural key: a repeat POST replaces the row and reports
        `replaced=True` instead of duplicating (spec 0058 rollout). The secret
        text wraps per type before sealing (`api_key` → `{"api_key"}`,
        `bearer`/`oauth2` → `{"token"}`, `basic` → `{"password"}`,
        `custom` → `{"headers"}` from caller JSON, `none` → `{}`).

        Args:
            provider: Partner name (code/DB constant at call sites).
            credential_type: `none|api_key|bearer|basic|oauth2|custom`.
            secret: Secret text (custom: JSON object text of static headers).
            auth_config: NON-secret placement (`location`/`name`/`username` —
                secret-looking keys are refused, never stored).
            expires_at: Token expiry, or `None` for non-expiring keys.

        Returns:
            `(row, replaced)`: the persisted row and whether one existed.

        Raises:
            InvalidToolError: When the shape is unknown, `auth_config`
                smuggles secret-looking keys, or custom JSON is not an
                object (HTTP 400).
        """
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
        existing = [row for row in await self._tenant.find_credentials_by_provider(provider)]
        replaced = bool(existing)
        row = PartnerCredential(
            provider=provider,
            credential_type=credential_type,
            auth_config=config,
            encrypted_data=envelope,
            key_version=_INITIAL_KEY_VERSION,
            expires_at=expires_at,
        )
        if existing:
            row.id = existing[0].id
        saved = await self._tenant.save_credential(row)
        logger.info("credential connected for provider")
        return saved, replaced

    async def revoke_credential(self, credential_id: str) -> None:
        """Soft-delete one credential row (revoke); foreign rows read as missing.

        Args:
            credential_id: Storage id.

        Raises:
            ToolNotFoundError: When no visible row exists (HTTP 404, no oracle).
        """
        try:
            deleted = await self._tenant.delete_credential(credential_id)
        except _StoreNotFoundError:
            deleted = False
        if not deleted:
            raise ToolNotFoundError(
                f"Credential {credential_id!r} not found.",
                details={"credential_id": credential_id},
            )
        logger.info("credential revoked")

    async def list_integrations(self, *, agent_id: str | None = None) -> list[PartnerIntegration]:
        """List own-tenant links, optionally narrowed to one agent.

        Args:
            agent_id: Narrow to one agent's links, or all.

        Returns:
            Active rows, oldest first.
        """
        rows = list(await self._tenant.list_integrations())
        if agent_id is not None:
            rows = [row for row in rows if row.agent_id == agent_id]
        return rows

    async def link_integration(self, *, provider: str, agent_id: str, config: dict[str, Any]) -> PartnerIntegration:
        """Link a provider to an agent; requires a live credential first.

        Args:
            provider: Partner name (must have a live credential row).
            agent_id: Agent this link belongs to.
            config: Non-secret runtime options (secrets stay in credentials).

        Returns:
            The persisted link row.

        Raises:
            InvalidToolError: When no live credential exists (HTTP 400,
                identifiers only).
        """
        live = await self._tenant.find_credentials_by_provider(provider)
        if not live:
            raise InvalidToolError(
                f"Provider {provider!r} has no connected credential.",
                details={"provider": provider},
            )
        saved = await self._tenant.save_integration(
            PartnerIntegration(provider=provider, agent_id=agent_id, config=dict(config))
        )
        logger.info("integration linked for provider")
        return saved

    async def unlink_integration(self, integration_id: str) -> None:
        """Soft-delete one link row; foreign rows read as missing.

        Args:
            integration_id: Storage id.

        Raises:
            ToolNotFoundError: When no visible row exists (HTTP 404, no oracle).
        """
        try:
            deleted = await self._tenant.delete_integration(integration_id)
        except _StoreNotFoundError:
            deleted = False
        if not deleted:
            raise ToolNotFoundError(
                f"Integration {integration_id!r} not found.",
                details={"integration_id": integration_id},
            )

    async def dcr_register(self, *, issuer_url: str, provider: str) -> tuple[PartnerCredential, Any]:
        """Register at an issuer and store the issued secret as a credential.

        Args:
            issuer_url: Provider issuer URL (SSRF-checked inside DCR).
            provider: Partner name the credential is stored under.

        Returns:
            `(credential_row, client_record)`: the persisted row and the
            issued record (callers surface `client_secret` once, never store it).

        Raises:
            ToolExecutionError: When discovery fails or MANUAL is required.
            InvalidToolError: When DCR is unwired in this composition.
        """
        if self._dcr is None:
            logger.warning("dcr register unwired: refusing outbound registration")
            raise InvalidToolError(
                "Dynamic registration is not wired in this deployment.",
                details={"issuer_url": issuer_url},
            )
        record = await self._dcr.register_client(issuer_url)
        row, _ = await self.connect_credential(
            provider=provider,
            credential_type=C.CREDENTIAL_TYPE_OAUTH2,
            secret=record.client_secret or "",
        )
        logger.info("dcr client stored for provider")
        return row, record

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
