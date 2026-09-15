"""Shared private helpers for the platform submodule.

Tenancy, not-found, and AuthZ helpers used by services (and thin
controllers). Pure functions of explicit arguments — no store, no
app.state, no environment access.
"""

from __future__ import annotations

from typing import Optional

from voiceai.platform.auth import Principal
from voiceai.platform.constants import DEFAULT_ORG_ID
from voiceai.platform.exceptions import AuthorizationError, HTTPException


def org_of(principal: Principal) -> str:
    """Return the caller's org, falling back to the default org.

    Args:
        principal: The authenticated caller.

    Returns:
        The org id to scope queries to.
    """
    return principal.org_id or DEFAULT_ORG_ID


def same_org(principal: Principal, resource_org: Optional[str]) -> bool:
    """Check whether a resource belongs to the caller's org.

    Args:
        principal: The authenticated caller.
        resource_org: The resource's owning org (None means default).

    Returns:
        True when the orgs match.
    """
    return (resource_org or DEFAULT_ORG_ID) == org_of(principal)


def require_same_org(principal: Principal, resource_org: Optional[str], resource: str = "resource") -> None:
    """Enforce tenancy: strict org match, 403 on cross-org access.

    Args:
        principal: The authenticated caller.
        resource_org: The resource's owning org (None means default).
        resource: Human-readable resource label for the error.

    Raises:
        AuthorizationError: If the resource belongs to another org.
    """
    expected = org_of(principal)
    actual = resource_org or DEFAULT_ORG_ID
    if actual != expected:
        raise AuthorizationError(f"{resource} belongs to another organization")


def not_found(resource: str, resource_id: str) -> HTTPException:
    """Build the standard 404 for a missing resource.

    Args:
        resource: Human-readable resource label.
        resource_id: The requested identifier.

    Returns:
        An HTTPException mapped to the error envelope upstream.
    """
    return HTTPException(status_code=404, detail=f"{resource} {resource_id} not found")
