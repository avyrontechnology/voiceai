"""Wire DTOs for the auth module: every request/response shape, strictly typed (T2).

Talko parity (`TalkoContract` in each component's `dto.py`): moved VERBATIM from
`controller.py` (same names, same fields, same constraints) plus the JWT additions
(`LoginResponse` carries the token pair the password flow returns). Controllers
reference these as `response_model` and request bodies; services and repositories
never import this module (the DTO direction is controller-in only).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from voiceai.common.constants import EMAIL_PATTERN
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.user import UserRole

__all__ = ["AuthContract"]


class AuthContract:
    """Namespace for the auth wire shapes (talko `TalkoContract` shape, strict)."""

    class SignupRequest(BaseModel):
        """First-user registration body."""

        email: str = Field(..., pattern=EMAIL_PATTERN)
        name: str | None = Field(None, min_length=1)
        password: str = Field(..., min_length=8, max_length=128)

    class LoginRequest(BaseModel):
        """Credential body; `remember` stretches the legacy session to 30 days."""

        email: str = Field(..., pattern=EMAIL_PATTERN)
        password: str = Field(..., min_length=1, max_length=128)
        remember: bool = Field(False, description="Extend the session to 30 days instead of the default week.")

    class InviteRequest(BaseModel):
        """Invite creation body."""

        email: str = Field(..., pattern=EMAIL_PATTERN)
        name: str | None = Field(None, min_length=1)
        role: UserRole = "member"

    class AcceptInviteRequest(BaseModel):
        """Invite redemption body."""

        token: str = Field(..., min_length=1)
        name: str | None = Field(None, min_length=1)
        password: str = Field(..., min_length=8, max_length=128)

    class SetRoleRequest(BaseModel):
        """Role-change body."""

        role: UserRole

    class ChangePasswordRequest(BaseModel):
        """Password-rotation body."""

        current_password: str = Field(..., min_length=1, max_length=128)
        new_password: str = Field(..., min_length=8, max_length=128)

    class UserResponse(BaseModel):
        """Client-safe user ledger shape (no hash, no internals)."""

        user_id: str
        email: str
        name: str | None = None
        role: UserRole
        org_id: str = "default"
        disabled: bool = False
        created_at: datetime
        last_login_at: datetime | None = None

    class UserListResponse(BaseModel):
        """User ledger listing."""

        users: list[AuthContract.UserResponse]

    class AuthMeResponse(BaseModel):
        """Caller identity plus effective scopes."""

        user: AuthContract.UserResponse
        scopes: list[str] = Field(default_factory=list)

    class LoginResponse(BaseModel):
        """Password-flow answer: identity plus the JWT token pair (T2).

        `access_token` is short-lived and kept in memory; the refresh token rides
        the `otoba_refresh` httpOnly cookie (never the body).
        """

        user: AuthContract.UserResponse
        scopes: list[str] = Field(default_factory=list)
        access_token: str = Field(..., description="Short-lived JWT, Authorization: Bearer.")
        token_type: str = Field("bearer", description="Authorization scheme for the access token.")
        expires_in: int = Field(..., ge=1, description="Access-token lifetime in seconds.")

    class CreateInviteResponse(BaseModel):
        """Issued invite plus its raw token (shown once)."""

        invite_id: str
        email: str
        role: UserRole
        token: str = Field(..., description="Raw invite token, shown once. Accept via POST /auth/accept.")
        expires_at: datetime

    class InviteListResponse(BaseModel):
        """Pending invites (never token hashes)."""

        invites: list[Invite]

    class WsTicketResponse(BaseModel):
        """Single-use websocket ticket, valid 60s."""

        ticket: str = Field(..., description="Single-use websocket ticket, valid 60s.")
        expires_in: int = 60

    class AuthEventListResponse(BaseModel):
        """Recent audit events, newest first."""

        events: list[AuthEvent]
