"""Auth schema package: explicit re-export of the moved models (spec 0005, C2)."""

from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.membership import Membership
from voiceai.modules.auth.models.organization import Organization
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionKind, SessionRecord
from voiceai.modules.auth.models.team import Team
from voiceai.modules.auth.models.tenant import Tenant
from voiceai.modules.auth.models.user import ALL_SCOPES, ROLE_RANK, ROLE_SCOPES, User, UserRole

__all__ = [
    "ALL_SCOPES",
    "ROLE_RANK",
    "ROLE_SCOPES",
    "ApiKey",
    "AuthEvent",
    "Invite",
    "Membership",
    "Organization",
    "Principal",
    "RevokedToken",
    "SessionKind",
    "SessionRecord",
    "Team",
    "Tenant",
    "User",
    "UserRole",
]
