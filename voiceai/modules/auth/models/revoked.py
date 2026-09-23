"""Revocation schema: denied JWT access-token identifiers (T2 greenfield).

Access tokens are stateless, so a single logout revokes by writing the token's `jti`
here (TTL index on `expires_at` bounds storage to the access-token lifetime).
Version bumps on the user row (`token_version`) revoke in bulk; this collection
covers the single-token case. The repository pins `id` to `jti`.
"""

from __future__ import annotations

from datetime import datetime

from voiceai.database.base import BaseFields

__all__ = ["RevokedToken"]


class RevokedToken(BaseFields):
    """One denied access token, keyed by its JWT id."""

    jti: str
    user_id: str
    expires_at: datetime
