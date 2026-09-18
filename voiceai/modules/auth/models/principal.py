"""Caller principal: session users and API keys behind one gate interface (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/auth.py`` (the one model living outside
``platform/models.py``); role data rides ``models/user.py`` in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from voiceai.modules.auth.models.user import ROLE_RANK, ROLE_SCOPES

__all__ = ["Principal"]


@dataclass
class Principal:
    """Caller identity behind one gate interface for sessions and API keys."""

    user_id: str | None
    email: str | None
    org_id: str = "default"
    role: str = "viewer"
    auth_type: str = "session"  # "session" | "key"
    scopes: list[str] = field(default_factory=list)
    key_id: str | None = None
    key_name: str | None = None

    def effective_scopes(self) -> list[str]:
        """Return key scopes verbatim, else the role's scope table (unknown roles grant nothing)."""
        if self.auth_type == "key":
            return self.scopes
        return ROLE_SCOPES.get(self.role, [])

    def has_scope(self, scope: str) -> bool:
        """Return whether `scope` (or the owner wildcard) is granted."""
        scopes = self.effective_scopes()
        return "*" in scopes or scope in scopes

    def has_role(self, minimum: str) -> bool:
        """Return whether the session role meets `minimum` (keys need the wildcard)."""
        if self.auth_type == "key":
            return "*" in self.scopes
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK.get(minimum, 99)
