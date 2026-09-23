"""Session schema: server-side session records (spec 0005, C2; T2 greenfield).

Greenfield deltas (T2): inherits :class:`voiceai.database.base.BaseFields`; the
repository pins `id` to `token_hash`. `kind` gains ``refresh`` — opaque rotating
refresh tokens (the JWT access token itself is stateless and never stored).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from voiceai.database.base import BaseFields

__all__ = ["SessionKind", "SessionRecord"]

#: Session-record kinds on the shared ledger: login sessions, single-use ws
#: tickets, and opaque JWT refresh tokens.
SessionKind = Literal["session", "ws-ticket", "refresh"]


class SessionRecord(BaseFields):
    """Server-side session record keyed by token hash."""

    token_hash: str
    user_id: str
    org_id: str = "default"
    kind: SessionKind = "session"
    expires_at: datetime
    #: JWT revocation stamp copied from the user row at mint; a refresh whose stamp
    #: trails the row was rotated out by a password change or logout-all.
    token_version: int = 0
