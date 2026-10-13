"""Pure tool helpers: natural keys, schema checks, fingerprints, scope predicates (specs 0029, 0056, 0081).

No I/O, no imports beyond stdlib + constants + models — deterministic and
unit-tested. Visibility decisions live here so repositories and services share
one truth; logging of those decisions lives with the callers (this module
never logs, so predicates stay total and side-effect free).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.modules.tools.constants import (
    BINDING_ORG_SHARED,
    BINDING_USER_PRIVATE,
    MCP_NAMESPACE_SEPARATOR,
    MCP_PREFIX,
    POLICY_ENFORCE_SHARED,
    POLICY_SHARED_WITH_OVERRIDE,
    POLICY_USER_CHOICE,
    TOOL_ID_SEPARATOR,
    VISIBILITY_GLOBAL,
)
from voiceai.modules.tools.models import (
    IntegrationTemplate,
    OwnerScope,
    PartnerCredential,
    PartnerIntegration,
    ToolDefinition,
)

__all__ = [
    "build_tool_id",
    "credential_visible_to",
    "effective_binding_set",
    "fingerprint_tool_call",
    "is_legacy_credential",
    "is_legacy_template",
    "is_system_row",
    "legacy_template_owner",
    "link_visible_to",
    "policy_allows_binding",
    "strip_mcp_prefix",
    "template_visible_to",
    "tenant_allows",
    "tool_visible_in_org",
    "validate_arguments",
]


def build_tool_id(kind: str, name: str) -> str:
    """Build the natural key `{kind}:{name}`.

    Args:
        kind: Tool kind (`function`, `webhook`, `internal`).
        name: Human label slug.

    Returns:
        The tool natural key (also pinned as the document `id`).
    """
    return TOOL_ID_SEPARATOR.join((kind, name))


def is_system_row(tenant_id: str | None) -> bool:
    """Return whether a row is system-owned (read-only to tenants).

    Args:
        tenant_id: The row's tenant stamp.

    Returns:
        `True` exactly for the system tenant.
    """
    return tenant_id == SYSTEM_TENANT_ID


def _matches_json_type(value: Any, expected: str) -> bool:  # why: JSON values are open-typed
    """Report whether a JSON value satisfies one JSON-Schema `type` name.

    Args:
        value: The argument value.
        expected: The schema `type` (`string`, `integer`, `number`,
            `boolean`, `array`, `object`, `null`).

    Returns:
        `True` when the value matches — and always `True` for an unknown
        type name, which is out of this subset's scope, never a finding.
    """
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def validate_arguments(schema: Mapping[str, Any], args: Mapping[str, Any]) -> list[str]:  # why: JSON-Schema is open
    """Validate call arguments against a JSON-Schema `parameters` contract (subset).

    Covers the object shape tool rows carry: `required` presence plus
    per-key `properties.{name}.type` checks. Anything richer (nesting,
    formats, `allOf`) is out of scope — unknown keywords are ignored, never
    flagged — so this stays a total, dependency-free function.

    Args:
        schema: The tool's `parameters` contract (empty means unconstrained).
        args: The call arguments.

    Returns:
        Problem strings naming schema paths only (never argument values).
    """
    problems: list[str] = []
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in args:
                problems.append(f"missing required: {name}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            if not isinstance(name, str) or name not in args:
                continue
            expected = subschema.get("type") if isinstance(subschema, dict) else None
            if isinstance(expected, str) and not _matches_json_type(args[name], expected):
                problems.append(f"{name}: expected {expected}")
    return problems


def fingerprint_tool_call(name: str, args: Mapping[str, Any]) -> str:  # why: JSON arguments are free-form
    """Return a stable idempotency fingerprint for one tool call.

    Args:
        name: The canonical tool id.
        args: The call arguments (canonicalized with sorted keys;
            non-serializable values degrade to `str` instead of raising).

    Returns:
        Lowercase SHA-256 hex digest over the name plus canonical arguments.
    """
    canonical = json.dumps(dict(args), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()


def strip_mcp_prefix(namespaced: str) -> str:
    """Strip the MCP registry namespace, returning the canonical tool id.

    Registries surface foreign tools as `mcp:{server}:{canonical}` where the
    canonical id keeps its own `{kind}:{name}` shape — so the prefix plus the
    server segment go, and everything after the server stays intact.

    Args:
        namespaced: Possibly namespaced id (`mcp:{server}:{canonical}`).

    Returns:
        The canonical id when prefixed, else the input unchanged.
    """
    if not namespaced.startswith(MCP_PREFIX):
        return namespaced
    rest = namespaced[len(MCP_PREFIX) :]
    _, separator, canonical = rest.partition(MCP_NAMESPACE_SEPARATOR)
    return canonical if separator else rest


def effective_binding_set(allowed: Sequence[str], policy: str) -> frozenset[str]:
    """Intersect template bindings with org policy (spec 0081, pure).

    `user_choice` and `shared_with_override` both admit the two bindings
    (they differ in UX defaulting, decided in P2 — not here); `enforce_shared`
    admits org reuse only. Unknown policies admit nothing (fail closed).

    Args:
        allowed: The template's `allowed_bindings`.
        policy: The org's `binding_policy`.

    Returns:
        The connectable bindings, possibly empty (callers raise 422).
    """
    policy_set: set[str]
    if policy == POLICY_ENFORCE_SHARED:
        policy_set = {BINDING_ORG_SHARED}
    elif policy in (POLICY_USER_CHOICE, POLICY_SHARED_WITH_OVERRIDE):
        policy_set = {BINDING_ORG_SHARED, BINDING_USER_PRIVATE}
    else:
        return frozenset()
    return frozenset(item for item in allowed if item in policy_set)


def is_legacy_credential(credential: PartnerCredential) -> bool:
    """Return whether a credential predates owner scoping (spec 0081, P1).

    Args:
        credential: The stored row.

    Returns:
        `True` exactly when `binding` is `None` — manageable in listings,
        invisible to execution (re-choose at the P2 boundary).
    """
    return credential.binding is None


def is_legacy_template(template: IntegrationTemplate) -> bool:
    """Return whether a template predates owner scoping (spec 0081, P1).

    Args:
        template: The stored row.

    Returns:
        `True` exactly when `owner_partner_id` is `None` (mapped from the
        tenant stamp on read, stamped on first write).
    """
    return template.owner_partner_id is None


def legacy_template_owner(tenant_id: str | None) -> str | None:
    """Map a legacy template's tenant stamp to its owner (spec 0081, P1).

    Tenancy here is partner-scoped (the tenant id IS the partner boundary),
    so the mapping is total: system rows stay system, every other tenant
    reads as its own partner.

    Args:
        tenant_id: The row's tenant stamp.

    Returns:
        `"system"` for system rows, else the tenant id (or `None` when the
        stamp itself is missing — still legacy, still mapped on write).
    """
    if tenant_id is None:
        return None
    if tenant_id == SYSTEM_TENANT_ID:
        return SYSTEM_TENANT_ID
    return tenant_id


def policy_allows_binding(binding: str, policy: str) -> bool:
    """Report whether an org policy admits one binding (spec 0081, P2, pure).

    Args:
        binding: The requested binding.
        policy: The org's `binding_policy`.

    Returns:
        `True` for org reuse under any policy and for private use except
        under `enforce_shared`; `False` for unknown policies (fail closed).
    """
    if binding == BINDING_ORG_SHARED:
        return policy in (POLICY_USER_CHOICE, POLICY_SHARED_WITH_OVERRIDE, POLICY_ENFORCE_SHARED)
    if binding == BINDING_USER_PRIVATE:
        return policy in (POLICY_USER_CHOICE, POLICY_SHARED_WITH_OVERRIDE)
    return False


def credential_visible_to(credential: PartnerCredential, scope: OwnerScope, *, for_execution: bool) -> bool:
    """Decide whether one credential row answers in a scope (spec 0081, pure).

    Execution is strict (attributable or refused); manage listings are wide
    (org admins see metadata — secrets never ride projections — so revokes
    stay operable). Legacy (`binding=None`) rows never execute.

    Args:
        credential: The stored row (same-tenant view assumed — tenancy is
            enforced by the view, ownership here).
        scope: Who asks. `org_id=None` means partner-wide manage.
        for_execution: `True` for the resolution path, `False` for listings.

    Returns:
        `True` when the row answers in this scope on this path.
    """
    if credential.binding is None:
        return not for_execution
    if credential.binding == BINDING_ORG_SHARED:
        if scope.org_id is None:
            return not for_execution
        return credential.owner_org_id == scope.org_id
    if credential.binding == BINDING_USER_PRIVATE:
        if scope.org_id is None:
            return not for_execution
        if for_execution:
            return credential.owner_org_id == scope.org_id and credential.owner_user_id == scope.user_id
        return credential.owner_org_id == scope.org_id
    return False


def template_visible_to(template: IntegrationTemplate, scope: OwnerScope) -> bool:
    """Decide whether one template row answers in a scope (spec 0081, pure).

    Global rows answer everywhere; partner rows answer their owner only
    (legacy `owner_partner_id=None` rows map from the tenant stamp — the
    same-tenant view already guarantees the tenant, this checks the owner).

    Args:
        template: The stored row.
        scope: Who asks (partner boundary only — orgs share templates).

    Returns:
        `True` when the row is global or owned by this partner.
    """
    if template.visibility == VISIBILITY_GLOBAL:
        return True
    owner = template.owner_partner_id
    return owner is None or owner == scope.partner_id


def tenant_allows(row_tenant: str | None, scope: OwnerScope) -> bool:
    """Check a row's tenant stamp against the scope (spec 0081, pure).

    Views already isolate tenants; this re-check makes scope methods total
    even if a caller composes the wrong view (a mismatch then reads as
    missing and logs a warning at the call site — a wiring bug, never a leak).

    Args:
        row_tenant: The row's `tenant_id` stamp (`None` on pre-stamp rows).
        scope: Who asks.

    Returns:
        `True` for unstamped, system, or same-partner rows.
    """
    if row_tenant is None or row_tenant == SYSTEM_TENANT_ID:
        return True
    return row_tenant == scope.partner_id


def link_visible_to(link: PartnerIntegration, scope: OwnerScope) -> bool:
    """Decide whether one link row answers in a scope (spec 0081, pure).

    Links are manage-plane rows (execution re-checks the live credential in
    P2): legacy (`org_id=None`) rows answer listings so they can be
    re-homed, and partner-wide manage (`org_id=None` scope) sees every row
    in the tenant view.

    Args:
        link: The stored row (same-tenant view assumed).
        scope: Who asks.

    Returns:
        `True` when the row's org is unset, matches, or the caller manages
        partner-wide.
    """
    if scope.org_id is None:
        return True
    return link.org_id is None or link.org_id == scope.org_id


def tool_visible_in_org(tool: ToolDefinition, org_id: str | None) -> bool:
    """Decide whether one tool row answers for an org picker (spec 0081, pure).

    Args:
        tool: The stored row (same-tenant view assumed).
        org_id: The asking org, or `None` for the tenant-wide picker.

    Returns:
        `True` when the row is tenant-wide (`owner_org_id=None`) or pinned
        to this org.
    """
    if org_id is None:
        return True
    return tool.owner_org_id is None or tool.owner_org_id == org_id
