"""Caller principal: session users and API keys behind one gate interface (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/auth.py`` (the one model living outside
``platform/models.py``); role data rides ``models/user.py`` in this package.

Spec 0040 adds the tenant runtime: ``tenant_id`` (tenant object hex
post-migration; ``"system"`` keeps anonymous/system paths total) plus ``teams``
(team ids) and ``team_roles`` (team id → role) projected from memberships by
the service resolvers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from voiceai.modules.auth.models.user import ROLE_RANK, ROLE_SCOPES

__all__ = ["Principal"]


@dataclass
class Principal:
    """Caller identity behind one gate interface for sessions and API keys.

    `tenant_id`/`teams`/`team_roles` (spec 0040) are stamped by the service
    resolvers from the user row and its memberships; hand-built principals
    keep the `"system"` default, which never matches a real tenant hex.
    """

    user_id: str | None
    email: str | None
    org_id: str = "default"
    #: Tenant object hex post-migration; `"system"` keeps anonymous/system
    #: paths total (spec 0040).
    tenant_id: str = "system"
    role: str = "viewer"
    auth_type: str = "session"  # "session" | "key"
    scopes: list[str] = field(default_factory=list)
    #: Team ids from the user's memberships (spec 0040).
    teams: list[str] = field(default_factory=list)
    #: Team id → role map from the user's memberships (spec 0040).
    team_roles: dict[str, str] = field(default_factory=dict)
    key_id: str | None = None
    key_name: str | None = None
    #: JWT id of the access token this principal was resolved from (`None` for legacy
    #: sessions and API keys). Carried so logout can deny exactly this token.
    token_id: str | None = None

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
