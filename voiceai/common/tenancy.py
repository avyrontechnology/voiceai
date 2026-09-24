"""Ambient tenant context: typed tenancy for every request, socket, job and tick.

The context rides a :class:`contextvars.ContextVar` (same pattern as the request
id in ``logger.py``) so any call depth can resolve the tenant without threading
it through signatures. Nothing reads or writes it yet — producers (HTTP
middleware, channel handshakes, job runner, scheduler) land in M1b; this module
is the vocabulary they will share.

The one hard rule lives here: :func:`current_tenant` raises instead of falling
back. A silent default-tenant fallback would mix tenant data across requests,
and no retry, log line, or rollback can unmix it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

from voiceai.common.errors import TenantNotBoundError

__all__ = [
    "SYSTEM_TENANT_ID",
    "TenantContext",
    "bind_tenant",
    "current_tenant",
    "reset_tenant",
]

#: Owner of platform-global rows (templates, plans). Never a real tenant.
SYSTEM_TENANT_ID: Final[str] = "system"

#: ContextVar name, following the ``voiceai.<area>`` convention of the logger.
TENANT_CONTEXTVAR = "voiceai.tenant_context"

_TENANT: ContextVar[TenantContext | None] = ContextVar(TENANT_CONTEXTVAR, default=None)


@dataclass(frozen=True)
class TenantContext:
    """Who this execution belongs to (spec 0018 target shape, M1a vocabulary).

    Attributes:
        tenant_id: Billing and isolation boundary. Always explicit, never defaulted.
        request_id: Correlation id, supplied by the producer (middleware copies the
            logging request id; jobs copy the event id).
        workspace_id: Optional grouping inside a tenant; carried, not enforced
            (semantics explicitly deferred per spec 0018 — see M1b).
        principal_id: Acting user or API key id; ``None`` for system work.
        scopes: Granted scopes; empty means no grants yet, not full access.
        plan: Billing plan name driving quotas; producers resolve the real plan.
    """

    tenant_id: str
    request_id: str
    workspace_id: str | None = None
    principal_id: str | None = None
    scopes: frozenset[str] = frozenset()
    plan: str = "default"


def current_tenant() -> TenantContext:
    """Return the ambient tenant context.

    Returns:
        The bound context.

    Raises:
        TenantNotBoundError: When nothing bound one — a wiring bug (missing
            middleware, job payload without tenant), surfaced as opaque 500.
    """
    context = _TENANT.get()
    if context is None:
        raise TenantNotBoundError("no tenant bound for this execution")
    return context


@contextmanager
def bind_tenant(context: TenantContext) -> Iterator[None]:
    """Bind ``context`` for the enclosed scope, restoring the outer one after.

    Args:
        context: The tenant context to make ambient.

    Yields:
        Nothing; the previous binding (possibly none) is restored on exit,
        including across exceptions.
    """
    token = _TENANT.set(context)
    try:
        yield
    finally:
        _TENANT.reset(token)


def reset_tenant() -> None:
    """Clear the ambient binding (tests; teardown between cases)."""
    _TENANT.set(None)
