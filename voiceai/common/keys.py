"""The one true tenant-scoped key builder (spec 0019, M0).

Every Redis key carrying tenant data is built here and nowhere else, so key
format stays greppable and tenant-prefixing cannot be forgotten: keys read
``t:{tenant_id}:{namespace}:{parts...}``. Tenant resolution itself (ambient
context, middleware) lands in M1; this helper takes the tenant explicitly so
callers stay honest about where the value came from.
"""

from __future__ import annotations

__all__ = ["tenant_key"]


def tenant_key(tenant_id: str, namespace: str, *parts: str) -> str:
    """Build a tenant-scoped key: ``t:{tenant_id}:{namespace}[:{parts...}]``.

    Args:
        tenant_id: Owning tenant; empty values are a programming error.
        namespace: Key family, e.g. ``"call"``, ``"throttle"``, ``"denylist"``.
        *parts: Additional segments, e.g. call id, IP, token id.

    Returns:
        The colon-joined key, always prefixed ``t:{tenant_id}:``.

    Raises:
        ValueError: When tenant or namespace is empty — an unscoped key must
            never be built silently.
    """
    if not tenant_id:
        raise ValueError("tenant_key requires a non-empty tenant_id")
    if not namespace:
        raise ValueError("tenant_key requires a non-empty namespace")
    return ":".join(["t", tenant_id, namespace, *parts])
