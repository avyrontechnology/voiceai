"""Auth schema package: explicit re-export of the moved models (spec 0005, C2)."""

from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionKind, SessionRecord
from voiceai.modules.auth.models.user import ALL_SCOPES, ROLE_RANK, ROLE_SCOPES, User, UserRole

__all__ = [
    "ALL_SCOPES",
    "ROLE_RANK",
    "ROLE_SCOPES",
    "ApiKey",
    "AuthEvent",
    "Invite",
    "Principal",
    "RevokedToken",
    "SessionKind",
    "SessionRecord",
    "User",
    "UserRole",
]
