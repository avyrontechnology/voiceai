"""Tools mapping helpers: rows into wire shapes (AGENTS.md rule 1g)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from voiceai.modules.tools import constants as C
from voiceai.modules.tools.models import (
    IntegrationAuditLog,
    IntegrationTemplate,
    OrgPolicy,
    PartnerCredential,
    PartnerIntegration,
    ToolDefinition,
)

__all__ = [
    "validation_field_paths",
    "wire_audit",
    "wire_audit_list",
    "wire_credential_meta",
    "wire_credential_meta_list",
    "wire_integration",
    "wire_integration_list",
    "wire_policy",
    "wire_policy_list",
    "wire_template",
    "wire_template_list",
    "wire_tool",
    "wire_tool_list",
]


def wire_tool(tool: ToolDefinition) -> dict[str, Any]:
    """Render one row as JSON (auth pointers included, never raw secrets).

    Args:
        tool: The stored row.

    Returns:
        JSON-able mapping of the row.
    """
    return tool.model_dump(mode="json")


def wire_tool_list(tools: list[ToolDefinition]) -> dict[str, Any]:
    """Render rows as a `{"tools": [...]}` payload.

    Args:
        tools: The stored rows.

    Returns:
        JSON-able mapping with the single `tools` key.
    """
    return {"tools": [wire_tool(tool) for tool in tools]}


def wire_credential_meta(credential: PartnerCredential) -> dict[str, Any]:
    """Render one credential as metadata (spec 0058: ciphertext never leaves).

    The projection is field-explicit (not `model_dump` minus one key) so a
    future field cannot leak by accident — `encrypted_data` has no path here.

    Args:
        credential: The stored row.

    Returns:
        JSON-able metadata mapping (identifiers + expiry only).
    """
    return {
        "id": credential.id,
        "provider": credential.provider,
        "credential_type": credential.credential_type,
        "key_version": credential.key_version,
        "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
        C.WIRE_BINDING_KEY: credential.binding,
        C.WIRE_STATUS_KEY: credential.status,
        C.WIRE_OWNER_ORG_KEY: credential.owner_org_id,
        C.WIRE_OWNER_USER_KEY: credential.owner_user_id,
    }


def wire_credential_meta_list(credentials: list[PartnerCredential]) -> dict[str, Any]:
    """Render credential rows as a `{"credentials": [...]}` payload.

    Args:
        credentials: The stored rows.

    Returns:
        JSON-able mapping with the single `credentials` key.
    """
    return {"credentials": [wire_credential_meta(credential) for credential in credentials]}


def wire_integration(integration: PartnerIntegration) -> dict[str, Any]:
    """Render one agent link row (config is non-secret by contract).

    Args:
        integration: The stored row.

    Returns:
        JSON-able mapping of the row.
    """
    return {
        "id": integration.id,
        "provider": integration.provider,
        "agent_id": integration.agent_id,
        C.WIRE_ORG_ID_KEY: integration.org_id,
        "config": dict(integration.config),
        "status": integration.status,
        "negotiated_protocol_version": integration.negotiated_protocol_version,
    }


def wire_integration_list(integrations: list[PartnerIntegration]) -> dict[str, Any]:
    """Render link rows as an `{"integrations": [...]}` payload.

    Args:
        integrations: The stored rows.

    Returns:
        JSON-able mapping with the single `integrations` key.
    """
    return {"integrations": [wire_integration(integration) for integration in integrations]}


def wire_template(template: IntegrationTemplate) -> dict[str, Any]:
    """Render one provider template (curated config, no secrets by contract).

    Args:
        template: The stored row.

    Returns:
        JSON-able mapping of the row.
    """
    return {
        "provider": template.provider,
        "credential_type": template.credential_type,
        "transport": template.transport,
        "tools_template": [dict(entry) for entry in template.tools_template],
        "oauth_config": dict(template.oauth_config),
        "client_config": dict(template.client_config),
        "tenant_id": template.tenant_id,
        C.WIRE_VISIBILITY_KEY: template.visibility,
        "owner_partner_id": template.owner_partner_id,
        C.WIRE_VERSION_KEY: template.version,
        C.WIRE_LIFECYCLE_KEY: template.lifecycle,
        C.WIRE_ALLOWED_BINDINGS_KEY: list(template.allowed_bindings),
        C.WIRE_SUGGESTED_BINDING_KEY: template.suggested_binding,
    }


def wire_template_list(templates: list[IntegrationTemplate]) -> dict[str, Any]:
    """Render templates as a `{"templates": [...]}` payload.

    Args:
        templates: The stored rows.

    Returns:
        JSON-able mapping with the single `templates` key.
    """
    return {"templates": [wire_template(template) for template in templates]}


def wire_policy(policy: OrgPolicy) -> dict[str, Any]:
    """Render one org policy row (rulebook metadata, never secrets — there are none).

    Args:
        policy: The stored row.

    Returns:
        JSON-able mapping of the rule plus its change attribution.
    """
    return {
        "id": policy.id,
        C.WIRE_ORG_ID_KEY: policy.org_id,
        C.WIRE_POLICY_KEY: policy.binding_policy,
        "changed_by": policy.changed_by,
        C.WIRE_REASON_KEY: policy.reason,
    }


def wire_policy_list(policies: list[OrgPolicy]) -> dict[str, Any]:
    """Render policy rows as a `{"policies": [...]}` payload.

    Args:
        policies: The stored rows.

    Returns:
        JSON-able mapping with the single `policies` key.
    """
    return {"policies": [wire_policy(policy) for policy in policies]}


def wire_audit(event: IntegrationAuditLog) -> dict[str, Any]:
    """Render one governance event (identifiers + reason — never payloads).

    Args:
        event: The stored row.

    Returns:
        JSON-able mapping of the event.
    """
    return {
        "id": event.id,
        "actor": dict(event.actor),
        C.WIRE_ACTION_KEY: event.action,
        C.WIRE_TARGET_KEY: {"kind": event.target_kind, "id": event.target_id},
        C.WIRE_REASON_KEY: event.reason,
        C.WIRE_AT_KEY: event.created_at.isoformat(),
    }


def wire_audit_list(events: list[IntegrationAuditLog]) -> dict[str, Any]:
    """Render audit rows as an `{"audit": [...]}` payload.

    Args:
        events: The stored rows.

    Returns:
        JSON-able mapping with the single `audit` key.
    """
    return {"audit": [wire_audit(event) for event in events]}


def validation_field_paths(exc: ValidationError) -> list[str]:
    """Reduce a validation failure to its dotted field paths (spec 0049, hardened spec 0052).

    Locations are client-safe (they name the schema, not the input); the
    messages are not (pydantic echoes the offending value), so only the
    locations leave this function. A record without a `loc` sequence contributes
    nothing instead of raising `KeyError`, so a malformed record can never turn
    a 400 into a 500.

    Args:
        exc: The pydantic failure.

    Returns:
        Sorted, de-duplicated dotted paths such as `["kind", "parameters.x"]`.
    """
    paths: set[str] = set()
    for error in exc.errors():
        raw_loc: Any = error.get("loc", ())
        loc: tuple[Any, ...] | list[Any] = raw_loc if isinstance(raw_loc, (list, tuple)) else ()
        if loc:
            paths.add(".".join(str(part) for part in loc))
    return sorted(paths)
