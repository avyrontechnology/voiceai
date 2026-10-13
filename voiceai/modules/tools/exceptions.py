"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import (
    BindingRequiredError,
    PolicyConflictError,
    ToolDeniedError,
    ToolError,
    ToolExecutionError,
    ToolForbiddenError,
    ToolNotFoundError,
)
from voiceai.modules.tools.models import OwnerScope, ToolDefinition
from voiceai.modules.tools.static_methods import effective_binding_set, policy_allows_binding

__all__ = [
    "ensure_allowlisted",
    "ensure_arguments_valid",
    "ensure_binding_permitted",
    "ensure_binding_present",
    "ensure_effective_allowed",
    "ensure_endpoint_safe",
    "ensure_owner_scope",
    "ensure_system_immutable",
    "ensure_tool_executable",
    "ensure_tool_found",
]


def ensure_tool_found(value: Any | None, tool_id: str) -> Any:
    """Return the tool row, or raise 404 (foreign rows arrive as `None`).

    Args:
        value: The looked-up row, or `None`.
        tool_id: The addressed id (identifiers only, never payloads).

    Returns:
        The row, narrowed to non-`None`.

    Raises:
        ToolNotFoundError: When the row is missing (or foreign — no oracle).
    """
    if value is None:
        raise ToolNotFoundError(f"Tool {tool_id!r} not found.", details={"tool_id": tool_id})
    return value


def ensure_system_immutable(is_system_row: bool, tool_id: str) -> None:
    """Reject writes addressing system rows (spec 0029).

    Args:
        is_system_row: Whether the addressed row is system-owned.
        tool_id: The addressed id.

    Raises:
        ToolForbiddenError: When a tenant write targets a system row.
    """
    if is_system_row:
        raise ToolForbiddenError(
            f"System tool {tool_id!r} is read-only; curation happens in code review.",
            details={"tool_id": tool_id},
        )


def ensure_tool_executable(tool: ToolDefinition | None, tool_id: str) -> ToolDefinition:
    """Return the row the executor may run, or raise 404 (spec 0056).

    Deprecated rows stay executable here (grandfathered, like reads — the
    attach gate in `get_tool_for_attach` owns deprecation, not the hot path).

    Args:
        tool: The looked-up row, or `None`.
        tool_id: The addressed canonical id (identifiers only, never payloads).

    Returns:
        The row, narrowed to non-`None`.

    Raises:
        ToolNotFoundError: When the row is missing (or foreign — no oracle).
    """
    if tool is None:
        raise ToolNotFoundError(f"Tool {tool_id!r} not found.", details={C.LOG_KEY_TOOL_ID: tool_id})
    return tool


def ensure_allowlisted(tool_id: str, agent_id: str, tool_refs: Sequence[str]) -> None:
    """Reject execution of a tool outside the agent's attached refs (spec 0056).

    Args:
        tool_id: The canonical executed id.
        agent_id: The owning agent (the allowlist principal).
        tool_refs: The snapshot's attached ref ids.

    Raises:
        ToolDeniedError: When `tool_id` is not attached (HTTP 403, identifiers
            only — arguments never enter the message or details).
    """
    if tool_id not in tool_refs:
        raise ToolDeniedError(
            C.ALLOWLIST_DENY_TEMPLATE.format(tool_id=tool_id, agent_id=agent_id),
            details={C.LOG_KEY_TOOL_ID: tool_id, C.LOG_KEY_AGENT_ID: agent_id},
        )


def ensure_arguments_valid(problems: Sequence[str], tool_id: str) -> None:
    """Reject calls whose arguments fail the row's JSON-Schema contract (spec 0056).

    Args:
        problems: The `validate_arguments` findings (schema paths only).
        tool_id: The canonical executed id.

    Raises:
        ToolExecutionError: When any problem was found (HTTP 400).
    """
    if problems:
        raise ToolExecutionError(
            f"Tool {tool_id!r} arguments failed validation.",
            details={C.LOG_KEY_TOOL_ID: tool_id, C.DETAIL_KEY_FIELDS: list(problems)},
        )


def ensure_owner_scope(scope: OwnerScope) -> OwnerScope:
    """Return the scope, or raise 500 on a missing partner boundary (spec 0081).

    A missing `partner_id` is a wiring bug (unbound context reaching the
    store) — failing closed here is what keeps one tenant's rows out of
    another tenant's query. Callers never default the partner.

    Args:
        scope: The scope the repository call acts for.

    Returns:
        The scope, narrowed to a bound partner.

    Raises:
        ToolError: When no partner is bound — a server wiring bug, so the
            base INTERNAL_ERROR keeps the response an opaque 500 while the
            log line carries the identifiers.
    """
    if not scope.partner_id:
        raise ToolError(
            "No partner bound for this scoped call.",
            details={C.LOG_KEY_PARTNER: ""},
        )
    return scope


def ensure_binding_present(binding: str | None, provider: str) -> str:
    """Return the binding, or raise 422 when the connect omitted it (spec 0081).

    Args:
        binding: The submitted binding, or `None` when omitted.
        provider: Partner name (identifiers only, for the 422 details).

    Returns:
        The binding, narrowed to non-`None`.

    Raises:
        BindingRequiredError: When the binding was omitted (HTTP 422).
    """
    if binding is None:
        raise BindingRequiredError(
            f"Provider {provider!r} needs an explicit binding (org_shared or user_private).",
            details={C.VAULT_PROVIDER_FIELD: provider},
        )
    return binding


def ensure_binding_permitted(binding: str, policy: str, provider: str) -> str:
    """Return the binding, or raise 403 when the org policy forbids it (spec 0081, P2).

    This is the authorization half of the 403/422 split: a valid binding the
    caller's org policy does not admit (e.g. `user_private` under
    `enforce_shared`) is a refusal of the actor — not a malformed request.

    Args:
        binding: The requested binding (present — see `ensure_binding_present`).
        policy: The org's `binding_policy`.
        provider: Partner name (identifiers only, for the 403 details).

    Returns:
        The binding unchanged.

    Raises:
        ToolForbiddenError: When the policy does not admit the binding
            (HTTP 403 with guidance to contact the org admin).
    """
    if not policy_allows_binding(binding, policy):
        raise ToolForbiddenError(
            f"Binding {binding!r} is not permitted under this org's policy — contact the org admin.",
            details={C.VAULT_PROVIDER_FIELD: provider, C.WIRE_POLICY_KEY: policy},
        )
    return binding


def ensure_effective_allowed(allowed: list[str], policy: str, provider: str) -> frozenset[str]:
    """Return the connectable bindings, or raise 422 on an empty set (spec 0081).

    Args:
        allowed: The template's `allowed_bindings`.
        policy: The org's `binding_policy`.
        provider: Partner name (identifiers only, for the 422 details).

    Returns:
        The non-empty effective set (`allowed ∩ policy`).

    Raises:
        PolicyConflictError: When nothing satisfies both (HTTP 422).
    """
    effective = effective_binding_set(allowed, policy)
    if not effective:
        raise PolicyConflictError(
            f"Provider {provider!r} cannot connect under policy {policy!r}.",
            details={C.VAULT_PROVIDER_FIELD: provider, C.WIRE_POLICY_KEY: policy},
        )
    return effective


def ensure_endpoint_safe(is_safe: bool, tool_id: str) -> None:
    """Reject calls whose endpoint fails the SSRF pre-flight (spec 0056).

    Args:
        is_safe: The `is_safe_outbound_url` verdict for the endpoint.
        tool_id: The canonical executed id (the URL itself never leaves here —
            error opacity: logs and clients name the tool, never the address).

    Raises:
        ToolExecutionError: When the endpoint is unsafe (HTTP 400).
    """
    if not is_safe:
        raise ToolExecutionError(
            f"Tool {tool_id!r} endpoint failed safety validation.",
            details={C.LOG_KEY_TOOL_ID: tool_id},
        )
