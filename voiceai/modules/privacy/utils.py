"""Privacy boundary utilities: the audit digest over caller addresses (spec 0071).

A raw caller IP is personal data under GDPR, so the module persists only a
one-way SHA-256 digest — derived here, once, so the service and any future
retention tooling share the same definition of the stored form.
"""

from __future__ import annotations

import hashlib

__all__ = ["caller_ip_hash"]


def caller_ip_hash(ip: str | None) -> str | None:
    """Return the stored-form digest of a caller IP, or `None` when absent.

    Args:
        ip: Caller IP as the transport saw it, or `None` when the request
            carried no client address (unix sockets, test clients).

    Returns:
        Lowercase SHA-256 hex digest, or `None` — the row persists the
        digest, never the raw address.
    """
    if not ip:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()
