"""HTTP surface for the auth module: wiring only, no logic (AGENTS.md rule 1f; T2 greenfield).

T2 deltas: wire shapes live in `schemas.AuthContract` (talko parity); cookie flags
come from `Environment` through the container (the `adapters/cookies.py` bridge is
deleted); login/signup/accept answer the JWT pair (`LoginResponse` + refresh
cookie) beside the legacy session cookie (dual-write until the T7 cutover); new
`POST /refresh` rotates; logout revokes both cookies' tokens.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from voiceai.common.errors import AppError
from voiceai.common.logger import get_logger
from voiceai.common.responses import error_response, success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.core.environment import Environment
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.errors import InvalidCredentialsError
from voiceai.modules.auth.helpers import public_user
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.schemas import AuthContract
from voiceai.modules.auth.service import AuthService, SessionTokens

__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["Auth"])


# -- envelopes (request shapes in, response shapes out) ------------------------
# Canonical definitions live in `schemas.AuthContract`; these aliases keep the
# handler bodies readable and make the move reviewable as pure relocation.
SignupRequest = AuthContract.SignupRequest
LoginRequest = AuthContract.LoginRequest
InviteRequest = AuthContract.InviteRequest
AcceptInviteRequest = AuthContract.AcceptInviteRequest
SetRoleRequest = AuthContract.SetRoleRequest
ChangePasswordRequest = AuthContract.ChangePasswordRequest
UserResponse = AuthContract.UserResponse
UserListResponse = AuthContract.UserListResponse
AuthMeResponse = AuthContract.AuthMeResponse
LoginResponse = AuthContract.LoginResponse
CreateInviteResponse = AuthContract.CreateInviteResponse
InviteListResponse = AuthContract.InviteListResponse
WsTicketResponse = AuthContract.WsTicketResponse
AuthEventListResponse = AuthContract.AuthEventListResponse


# -- seams --------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """Extract the client address, preferring the leftmost forwarded entry."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


ServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]
EnvDep = Annotated[Environment, Depends(Provide[VoiceAIContainer.environment])]


@inject
async def get_principal(request: Request, service: ServiceDep) -> Principal:
    """Resolve the caller from session cookie, else Bearer key (401 when neither)."""
    return await service.authenticate(request.cookies.get(C.SESSION_COOKIE), request.headers.get("authorization", ""))


def _to_http(exc: AppError) -> JSONResponse:
    """Render a service error as the standard error envelope (C6 enumerates this map)."""
    return error_response(exc)


def _cookie_flags(env: Environment) -> dict[str, object]:
    """Resolve the session-cookie flags from the environment (T2 owns the knobs).

    Args:
        env: The process configuration carrying `cookie_secure*`/`cookie_domain`.

    Returns:
        The `secure`/`samesite`/`domain` kwargs for `set_cookie` (`domain=None`
        keeps the host-only behavior when no domain is configured).
    """
    return {
        "secure": env.cookie_secure_effective,
        "samesite": env.cookie_samesite,
        "domain": env.cookie_domain or None,
    }


def _set_session_cookie(response: Response, token: str, ttl_s: int, env: Environment) -> None:
    """Attach the opaque legacy session cookie (flags from `Environment`, T2)."""
    response.set_cookie(
        C.SESSION_COOKIE,
        token,
        max_age=ttl_s,
        httponly=True,
        path="/",
        **_cookie_flags(env),  # type: ignore[arg-type]  # why: flag mapping is exact per _cookie_flags
    )


def _set_refresh_cookie(response: Response, token: str, ttl_s: int, env: Environment) -> None:
    """Attach the opaque refresh cookie (httpOnly, same flags as the session cookie)."""
    response.set_cookie(
        C.REFRESH_COOKIE,
        token,
        max_age=ttl_s,
        httponly=True,
        path="/",
        **_cookie_flags(env),  # type: ignore[arg-type]  # why: flag mapping is exact per _cookie_flags
    )


def _clear_auth_cookies(response: Response) -> None:
    """Drop both auth cookies (verbatim paths)."""
    response.delete_cookie(C.SESSION_COOKIE, path="/")
    response.delete_cookie(C.REFRESH_COOKIE, path="/")


def _me_response(user: User, scopes: list[str]) -> AuthMeResponse:
    """Shape the caller-identity payload clients pin."""
    return AuthMeResponse(user=UserResponse(**public_user(user)), scopes=scopes)


def _login_response(user: User, scopes: list[str], access_token: str, expires_in: int) -> LoginResponse:
    """Shape the password-flow answer: identity plus the JWT pair metadata."""
    return LoginResponse(
        user=UserResponse(**public_user(user)),
        scopes=scopes,
        access_token=access_token,
        token_type=C.BEARER_SCHEME,
        expires_in=expires_in,
    )


def _pair_cookies(result: Response, tokens: SessionTokens, *, remember: bool, env: Environment) -> None:
    """Attach the legacy session cookie plus the refresh cookie (dual-write, T2)."""
    _set_session_cookie(result, tokens.legacy_token, C.REMEMBER_TTL_S if remember else C.SESSION_TTL_S, env)
    _set_refresh_cookie(result, tokens.refresh_token, env.jwt_refresh_ttl_s, env)


def _session_scopes(user: User) -> list[str]:
    """Effective scopes of a fresh session principal (login/accept shape it inline)."""
    return Principal(
        user_id=user.user_id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        auth_type="session",
    ).effective_scopes()


PrincipalDep = Annotated[Principal, Depends(get_principal)]


# -- routes -------------------------------------------------------------------


@router.post("/signup", status_code=201, response_model=AuthContract.LoginResponse)
@inject
async def signup(payload: SignupRequest, service: ServiceDep, env: EnvDep) -> JSONResponse:
    """Register the first user (owner); later signups need an invite."""
    try:
        user, tokens = await service.signup(payload.email, payload.name, payload.password)
    except AppError as exc:
        return _to_http(exc)
    get_logger("auth").info("Owner signed up: %s", user.email)  # TODO(spec-0005): drop the email, log the user_id
    result = success_response(
        _login_response(user, _session_scopes(user), tokens.access_token, env.jwt_access_ttl_s),
        status_code=201,
    )
    _pair_cookies(result, tokens, remember=False, env=env)
    return result


@router.post("/login", response_model=AuthContract.LoginResponse)
@inject
async def login(payload: LoginRequest, request: Request, service: ServiceDep, env: EnvDep) -> JSONResponse:
    """Check credentials, set both cookies, return identity plus the access token."""
    try:
        user, tokens = await service.login(
            payload.email, payload.password, payload.remember, client_ip=client_ip(request)
        )
    except AppError as exc:
        return _to_http(exc)
    result = success_response(_login_response(user, _session_scopes(user), tokens.access_token, env.jwt_access_ttl_s))
    _pair_cookies(result, tokens, remember=payload.remember, env=env)
    return result


@router.post("/refresh", response_model=AuthContract.LoginResponse)
@inject
async def refresh(request: Request, service: ServiceDep, env: EnvDep) -> JSONResponse:
    """Rotate the refresh cookie into a fresh pair (single-use rotation)."""
    try:
        user, tokens = await service.refresh(request.cookies.get(C.REFRESH_COOKIE), client_ip=client_ip(request))
    except AppError as exc:
        return _to_http(exc)
    result = success_response(_login_response(user, _session_scopes(user), tokens.access_token, env.jwt_access_ttl_s))
    _pair_cookies(result, tokens, remember=True, env=env)
    return result


@router.post("/logout")
@inject
async def logout(request: Request, service: ServiceDep) -> JSONResponse:
    """Revoke both cookies' tokens (anonymous logout still clears them)."""
    try:
        principal = await service.authenticate(
            request.cookies.get(C.SESSION_COOKIE), request.headers.get("authorization", "")
        )
    except InvalidCredentialsError:
        principal = None
    try:
        await service.logout(
            request.cookies.get(C.SESSION_COOKIE),
            request.cookies.get(C.REFRESH_COOKIE),
            principal,
        )
    except AppError as exc:
        return _to_http(exc)
    result = success_response(dict(C.OK_BODY))
    _clear_auth_cookies(result)
    return result


@router.get("/me", response_model=AuthContract.AuthMeResponse)
@inject
async def me(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Return the session caller's identity (keys read 401 here)."""
    try:
        user, scopes = await service.me(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(_me_response(user, scopes))


@router.post("/invite", status_code=201)
@inject
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
@inject
async def list_invites(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """List pending invites (never token hashes)."""
    try:
        pending = await service.list_invites(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(InviteListResponse(invites=pending))


@router.delete("/invites/{invite_id}")
@inject
async def delete_invite(invite_id: str, principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Revoke an invite."""
    try:
        await service.delete_invite(principal, invite_id)
    except AppError as exc:
        return _to_http(exc)
    return success_response(dict(C.OK_BODY))


@router.post("/accept", status_code=201, response_model=AuthContract.LoginResponse)
@inject
async def accept_invite(payload: AcceptInviteRequest, service: ServiceDep, env: EnvDep) -> JSONResponse:
    """Redeem an invite token into a user plus a first token pair."""
    try:
        user, tokens = await service.accept_invite(payload.token, payload.name, payload.password)
    except AppError as exc:
        return _to_http(exc)
    result = success_response(
        _login_response(user, _session_scopes(user), tokens.access_token, env.jwt_access_ttl_s),
        status_code=201,
    )
    _pair_cookies(result, tokens, remember=False, env=env)
    return result


@router.get("/users")
@inject
async def list_users(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """List every user (admins only)."""
    try:
        users = await service.list_users(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(UserListResponse(users=[UserResponse(**public_user(u)) for u in users]))


@router.put("/users/{user_id}/role")
@inject
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
@inject
async def delete_user(user_id: str, principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Delete a user plus all their sessions (owners only)."""
    try:
        await service.delete_user(principal, user_id)
    except AppError as exc:
        return _to_http(exc)
    return success_response(dict(C.OK_BODY))


@router.put("/password")
@inject
async def change_password(
    payload: ChangePasswordRequest,
    principal: PrincipalDep,
    service: ServiceDep,
    env: EnvDep,
) -> JSONResponse:
    """Rotate the caller's password, re-issuing their cookies (all else dies)."""
    try:
        tokens = await service.change_password(
            principal,
            payload.current_password,
            payload.new_password,
        )
    except AppError as exc:
        return _to_http(exc)
    result = success_response(dict(C.OK_BODY))
    _pair_cookies(result, tokens, remember=False, env=env)
    return result


@router.post("/ws-ticket")
@inject
async def ws_ticket(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """Mint a short-lived single-use websocket ticket (calls scope only)."""
    try:
        ticket = await service.mint_ticket(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(WsTicketResponse(ticket=ticket, expires_in=C.WS_TICKET_TTL_S))


@router.get("/events")
@inject
async def auth_events(principal: PrincipalDep, service: ServiceDep) -> JSONResponse:
    """List recent audit events, newest first (admins only)."""
    try:
        events = await service.auth_events(principal)
    except AppError as exc:
        return _to_http(exc)
    return success_response(AuthEventListResponse(events=events))
