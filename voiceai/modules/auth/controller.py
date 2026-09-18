"""HTTP surface for the auth module: wiring only, no logic (AGENTS.md rule 1f).

Thirteen routes moved line-by-line from ``voiceai/platform/auth_router.py`` (spec
0005, C5); the refactor deltas are mechanical:

* Bodies delegate to ``AuthService`` (per-request over the app.state store seam);
  role/scope gates already live in the service with verbatim messages.
* Request/response envelopes live here (the agents-A4 precedent), with verbatim
  field constraints; payloads serialize identically to legacy inside the standard
  ``common.responses`` envelopes (rule 2) at identical statuses, with identical
  ``detail`` strings on errors — byte-identity holds at the payload level, and the
  endgame cutover spec owns any client migration.
* Every handler funnels ``AppError`` through the single ``_to_http`` mapper (the C6
  map test enumerates it); unexpected exceptions propagate to the factory's opaque-500
  backstop. ``get_store`` maps the legacy 503 seam onto ``DependencyUnavailableError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from voiceai.common.constants import EMAIL_PATTERN
from voiceai.common.errors import AppError, ConfigurationError, DependencyUnavailableError
from voiceai.common.logger import get_logger
from voiceai.common.responses import error_response, success_response
from voiceai.core.container import Container, get_container
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.adapters.cookies import COOKIE_SAMESITE, COOKIE_SECURE
from voiceai.modules.auth.errors import InvalidCredentialsError
from voiceai.modules.auth.helpers import public_user
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.user import User, UserRole
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.service import AuthService

__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["Auth"])


# -- envelopes (request shapes in, response shapes out) ------------------------


class SignupRequest(BaseModel):
    """First-user registration body."""

    email: str = Field(..., pattern=EMAIL_PATTERN)
    name: str | None = Field(None, min_length=1)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """Credential body; `remember` stretches the session to 30 days."""

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

    users: list[UserResponse]


class AuthMeResponse(BaseModel):
    """Caller identity plus effective scopes."""

    user: UserResponse
    scopes: list[str] = Field(default_factory=list)


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


# -- seams --------------------------------------------------------------------


def get_store(request: Request) -> AuthStorePort:
    """Read the platform store off app.state (spec 0006 E1 demotes this to the fallback).

    Reached only when the container has no `AuthStorePort` binding (partial
    compositions, legacy tests); spec 0006 E3 deletes this seam once the container owns
    every serving path.

    Args:
        request: The incoming request, carrying the app state.

    Returns:
        The store as the service's port — no legacy import needed (structural).

    Raises:
        DependencyUnavailableError: When the seam has no store (legacy 503 string).
    """
    store: AuthStorePort | None = getattr(request.app.state, "platform_store", None)
    if store is None:
        raise DependencyUnavailableError("Platform store unavailable")
    return store  # why: MemoryStore/RedisStore satisfy the port structurally


def get_service(request: Request, container: Annotated[Container, Depends(get_container)]) -> AuthService:
    """Resolve the service over the container store (AGENTS.md rule 9; spec 0006 E1).

    The container binding wins; a container without one falls back to the app.state
    seam, and requests still read 503 when neither exists. Route shapes, statuses and
    the `AppError` funnel are unchanged.

    Args:
        request: The incoming request, carrying the app.state fallback seam.
        container: The application container, injected by the core dependency.

    Returns:
        The auth service over the resolved store.
    """
    try:
        # The port class object is the key: abstract for mypy, hashable at runtime.
        return AuthService(container.resolve(AuthStorePort))  # type: ignore[type-abstract]
    except ConfigurationError:
        return AuthService(get_store(request))


def client_ip(request: Request) -> str:
    """Extract the client address, preferring the leftmost forwarded entry."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_principal(request: Request, service: Annotated[AuthService, Depends(get_service)]) -> Principal:
    """Resolve the caller from session cookie, else Bearer key (401 when neither)."""
    return await service.authenticate(request.cookies.get(C.SESSION_COOKIE), request.headers.get("authorization", ""))


def _to_http(exc: AppError) -> JSONResponse:
    """Render a service error as the standard error envelope (C6 enumerates this map)."""
    return error_response(exc)


def _set_session_cookie(response: Response, token: str, ttl_s: int) -> None:
    """Attach the opaque session cookie (verbatim flags, bridge-sourced)."""
    response.set_cookie(
        C.SESSION_COOKIE,
        token,
        max_age=ttl_s,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]  # why: bridge reads the legacy env string, as before
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Drop the session cookie (verbatim path)."""
    response.delete_cookie(C.SESSION_COOKIE, path="/")


def _me_response(user: User, scopes: list[str]) -> AuthMeResponse:
    """Shape the caller-identity payload clients pin."""
    return AuthMeResponse(user=UserResponse(**public_user(user)), scopes=scopes)


def _session_scopes(user: User) -> list[str]:
    """Effective scopes of a fresh session principal (login/accept shape it inline)."""
    return Principal(
        user_id=user.user_id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        auth_type="session",
    ).effective_scopes()


StoreDep = Annotated[AuthStorePort, Depends(get_store)]
ServiceDep = Annotated[AuthService, Depends(get_service)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


# -- routes -------------------------------------------------------------------


@router.post("/signup", status_code=201)
async def signup(payload: SignupRequest, service: ServiceDep) -> JSONResponse:
    """Register the first user (owner); later signups need an invite."""
    try:
        user, token = await service.signup(payload.email, payload.name, payload.password)
    except AppError as exc:
        return _to_http(exc)
    get_logger("auth").info("Owner signed up: %s", user.email)  # TODO(spec-0005): drop the email, log the user_id
    result = success_response(UserResponse(**public_user(user)), status_code=201)
    _set_session_cookie(result, token, C.SESSION_TTL_S)
    return result


@router.post("/login")
async def login(payload: LoginRequest, request: Request, service: ServiceDep) -> JSONResponse:
    """Check credentials, mint a session cookie, return the caller identity."""
    try:
        user, token = await service.login(
            payload.email, payload.password, payload.remember, client_ip=client_ip(request)
        )
    except AppError as exc:
        return _to_http(exc)
    result = success_response(_me_response(user, _session_scopes(user)))
    _set_session_cookie(result, token, C.REMEMBER_TTL_S if payload.remember else C.SESSION_TTL_S)
    return result


@router.post("/logout")
async def logout(request: Request, service: ServiceDep) -> JSONResponse:
    """Revoke the session cookie's token (anonymous logout still clears it)."""
    try:
        principal = await service.authenticate(
            request.cookies.get(C.SESSION_COOKIE), request.headers.get("authorization", "")
        )
    except InvalidCredentialsError:
        principal = None
    try:
        await service.logout(request.cookies.get(C.SESSION_COOKIE), principal)
    except AppError as exc:
        return _to_http(exc)
    result = success_response(dict(C.OK_BODY))
    _clear_session_cookie(result)
    return result


@router.get("/me")
async def me(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Return the session caller's identity (keys read 401 here)."""
    try:
        user, scopes = await service.me(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(_me_response(user, scopes))


@router.post("/invite", status_code=201)
async def invite(payload: InviteRequest, principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Create an invite; HYBRID delivery — the raw token rides the response."""
    try:
        created, token = await service.invite(principal, payload.email, payload.name, payload.role)
    except AppError as exc:
        return _to_http(exc)
    return success_response(
        CreateInviteResponse(
            invite_id=created.invite_id,
            email=created.email,
            role=created.role,
            token=token,
            expires_at=created.expires_at,
        ),
        status_code=201,
    )


@router.get("/invites")
async def list_invites(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """List pending invites (never token hashes)."""
    try:
        pending = await service.list_invites(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(InviteListResponse(invites=pending))


@router.delete("/invites/{invite_id}")
async def delete_invite(invite_id: str, principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Revoke an invite."""
    try:
        await service.delete_invite(principal, invite_id)
    except AppError as exc:
        return _to_http(exc)
    return success_response(dict(C.OK_BODY))


@router.post("/accept", status_code=201)
async def accept_invite(payload: AcceptInviteRequest, service: ServiceDep) -> JSONResponse:
    """Redeem an invite token into a user plus a first session cookie."""
    try:
        user, token = await service.accept_invite(payload.token, payload.name, payload.password)
    except AppError as exc:
        return _to_http(exc)
    result = success_response(_me_response(user, _session_scopes(user)), status_code=201)
    _set_session_cookie(result, token, C.SESSION_TTL_S)
    return result


@router.get("/users")
async def list_users(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """List every user (admins only)."""
    try:
        users = await service.list_users(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(UserListResponse(users=[UserResponse(**public_user(u)) for u in users]))


@router.put("/users/{user_id}/role")
async def set_user_role(
    user_id: str, payload: SetRoleRequest, principal: PrincipalDep, service: ServiceDep
) -> JSONResponse:
    """Change a user's role, revoking their sessions (owners only)."""
    try:
        target = await service.set_user_role(principal, user_id, payload.role)
    except AppError as exc:
        return _to_http(exc)
    return success_response(UserResponse(**public_user(target)))


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Delete a user plus all their sessions (owners only)."""
    try:
        await service.delete_user(principal, user_id)
    except AppError as exc:
        return _to_http(exc)
    return success_response(dict(C.OK_BODY))


@router.put("/password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    principal: PrincipalDep,
    service: ServiceDep,
) -> JSONResponse:
    """Rotate the caller's password, keeping only the current session."""
    try:
        await service.change_password(
            principal,
            payload.current_password,
            payload.new_password,
            request.cookies.get(C.SESSION_COOKIE),
        )
    except AppError as exc:
        return _to_http(exc)
    return success_response(dict(C.OK_BODY))


@router.post("/ws-ticket")
async def ws_ticket(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Mint a short-lived single-use websocket ticket (calls scope only)."""
    try:
        ticket = await service.mint_ticket(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(WsTicketResponse(ticket=ticket, expires_in=C.WS_TICKET_TTL_S))


@router.get("/events")
async def auth_events(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """List recent audit events, newest first (admins only)."""
    try:
        events = await service.auth_events(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(AuthEventListResponse(events=events))
