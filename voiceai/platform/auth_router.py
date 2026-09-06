"""Auth endpoints: signup/login/logout, invites, users, password, tickets, audit.

First user becomes owner; afterwards signup closes and admins invite.
See platform/auth.py for hashing, sessions, roles and scopes.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform import auth
from voiceai.platform.auth import (
    Principal,
    REMEMBER_TTL_S,
    SESSION_TTL_S,
    audit,
    check_login_allowed,
    client_ip,
    get_principal,
    get_store,
    hash_password,
    mint_session,
    mint_ws_ticket,
    new_token,
    public_user,
    require_principal,
    require_role,
    require_scope,
    revoke_session,
    token_hash,
    verify_password,
)
from voiceai.platform.models import (
    AcceptInviteRequest,
    AuthEventListResponse,
    AuthMeResponse,
    ChangePasswordRequest,
    CreateInviteResponse,
    Invite,
    InviteListResponse,
    InviteRequest,
    LoginRequest,
    SetRoleRequest,
    SignupRequest,
    User,
    UserListResponse,
    UserResponse,
    WsTicketResponse,
    new_id,
    utcnow,
)
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


def _me_response(user: User, scopes: list) -> AuthMeResponse:
    return AuthMeResponse(user=UserResponse(**public_user(user)), scopes=scopes)


async def _assert_last_owner_safe(store: MemoryStore, target: User) -> None:
    if target.role != "owner" or target.disabled:
        return
    owners = [u for u in await store.list_users() if u.role == "owner" and not u.disabled]
    if len(owners) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last active owner")


@auth_router.post("/signup", response_model=UserResponse, status_code=201)
async def signup(
    payload: SignupRequest, request: Request, response: Response, store: MemoryStore = Depends(get_store)
) -> UserResponse:
    if await store.count_users() > 0:
        raise HTTPException(status_code=403, detail="Signup is closed — ask an admin for an invite")
    if await store.get_user_by_email(payload.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        user_id=new_id("usr"),
        email=payload.email.strip().lower(),
        name=payload.name,
        password_hash=hash_password(payload.password),
        role="owner",
    )
    await store.save_user(user)
    await mint_session(store, user, response)
    await audit(store, "signup", user.user_id, user.email, "first user (owner)")
    logger.info(f"Owner signed up: {user.email}")
    return UserResponse(**public_user(user))


@auth_router.post("/login", response_model=AuthMeResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, store: MemoryStore = Depends(get_store)
) -> AuthMeResponse:
    check_login_allowed(client_ip(request))
    user = await store.get_user_by_email(payload.email)
    if not user or user.disabled or not verify_password(payload.password, user.password_hash):
        await audit(store, "login_failed", None, payload.email.strip().lower())
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await mint_session(store, user, response, ttl_s=REMEMBER_TTL_S if payload.remember else SESSION_TTL_S)
    user.last_login_at = utcnow()
    await store.save_user(user)
    await audit(store, "login", user.user_id, user.email)
    principal = Principal(user_id=user.user_id, email=user.email, org_id=user.org_id, role=user.role)
    return _me_response(user, principal.effective_scopes())


@auth_router.post("/logout")
async def logout(request: Request, response: Response, store: MemoryStore = Depends(get_store)) -> dict:
    principal = None
    try:
        principal = await get_principal(request, store)
    except HTTPException:
        pass
    await revoke_session(store, request, response)
    if principal and principal.user_id:
        await audit(store, "logout", principal.user_id, principal.email)
    return {"ok": True}


@auth_router.get("/me", response_model=AuthMeResponse)
async def me(
    principal: Principal = Depends(require_principal), store: MemoryStore = Depends(get_store)
) -> AuthMeResponse:
    if principal.auth_type != "session" or not principal.user_id:
        raise HTTPException(status_code=401, detail="Session required")
    user = await store.get_user(principal.user_id)
    if not user or user.disabled:
        raise HTTPException(status_code=401, detail="Session required")
    return _me_response(user, principal.effective_scopes())


@auth_router.post("/invite", response_model=CreateInviteResponse, status_code=201)
async def invite(
    payload: InviteRequest,
    principal: Principal = Depends(require_role("admin")),
    store: MemoryStore = Depends(get_store),
) -> CreateInviteResponse:
    if payload.role in ("owner", "admin") and principal.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can invite owners/admins")
    if await store.get_user_by_email(payload.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    token = new_token()
    invite = Invite(
        invite_id=new_id("inv"),
        email=payload.email.strip().lower(),
        name=payload.name,
        role=payload.role,
        token_hash=token_hash(token),
        expires_at=utcnow() + timedelta(seconds=auth.INVITE_TTL_S),
        created_by=principal.user_id,
    )
    await store.save_invite(invite)
    await audit(store, "invite", principal.user_id, principal.email, f"{invite.email} as {invite.role}")
    return CreateInviteResponse(
        invite_id=invite.invite_id, email=invite.email, role=invite.role, token=token, expires_at=invite.expires_at
    )


@auth_router.get("/invites", response_model=InviteListResponse)
async def list_invites(
    _: Principal = Depends(require_role("admin")), store: MemoryStore = Depends(get_store)
) -> InviteListResponse:
    # Never leak token hashes to the UI.
    return InviteListResponse(invites=[i for i in await store.list_invites() if not i.accepted])


@auth_router.delete("/invites/{invite_id}")
async def delete_invite(
    invite_id: str,
    principal: Principal = Depends(require_role("admin")),
    store: MemoryStore = Depends(get_store),
) -> dict:
    if not await store.delete_invite(invite_id):
        raise HTTPException(status_code=404, detail="Invite not found")
    await audit(store, "invite_revoked", principal.user_id, principal.email, invite_id)
    return {"ok": True}


@auth_router.post("/accept", response_model=AuthMeResponse, status_code=201)
async def accept_invite(
    payload: AcceptInviteRequest, response: Response, store: MemoryStore = Depends(get_store)
) -> AuthMeResponse:
    digest = token_hash(payload.token)
    invite = next(
        (i for i in await store.list_invites() if i.token_hash == digest and not i.accepted),
        None,
    )
    from datetime import timezone

    if not invite:
        raise HTTPException(status_code=400, detail="Invite invalid or expired")
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < utcnow():
        raise HTTPException(status_code=400, detail="Invite invalid or expired")
    if await store.get_user_by_email(invite.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        user_id=new_id("usr"),
        email=invite.email,
        name=payload.name or invite.name,
        password_hash=hash_password(payload.password),
        role=invite.role,
    )
    await store.save_user(user)
    invite.accepted = True
    await store.save_invite(invite)
    await mint_session(store, user, response)
    await audit(store, "invite_accepted", user.user_id, user.email, f"as {user.role}")
    principal = Principal(user_id=user.user_id, email=user.email, org_id=user.org_id, role=user.role)
    return _me_response(user, principal.effective_scopes())


@auth_router.get("/users", response_model=UserListResponse)
async def list_users(
    _: Principal = Depends(require_role("admin")), store: MemoryStore = Depends(get_store)
) -> UserListResponse:
    return UserListResponse(users=[UserResponse(**public_user(u)) for u in await store.list_users()])


@auth_router.put("/users/{user_id}/role", response_model=UserResponse)
async def set_user_role(
    user_id: str,
    payload: SetRoleRequest,
    principal: Principal = Depends(require_role("owner")),
    store: MemoryStore = Depends(get_store),
) -> UserResponse:
    target = await store.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    await _assert_last_owner_safe(store, target)
    target.role = payload.role
    await store.save_user(target)
    await store.delete_user_sessions(user_id)
    await audit(store, "role_change", principal.user_id, principal.email, f"{target.email} -> {payload.role}")
    return UserResponse(**public_user(target))


@auth_router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    principal: Principal = Depends(require_role("owner")),
    store: MemoryStore = Depends(get_store),
) -> dict:
    target = await store.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    await _assert_last_owner_safe(store, target)
    await store.delete_user_sessions(user_id)
    await store.delete_user(user_id)
    await audit(store, "user_deleted", principal.user_id, principal.email, target.email)
    return {"ok": True}


@auth_router.put("/password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_principal),
    store: MemoryStore = Depends(get_store),
) -> dict:
    if principal.auth_type != "session" or not principal.user_id:
        raise HTTPException(status_code=403, detail="Password change requires a login session")
    user = await store.get_user(principal.user_id)
    if not user or not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    await store.save_user(user)
    # Keep this session, kill the rest.
    current = request.cookies.get(auth.SESSION_COOKIE)
    keep = {token_hash(current)} if current else set()
    if hasattr(store, "_redis"):
        raws = await store._list_collection("sessions")
    else:
        raws = list(store._data.get("sessions", {}).values())
    for raw in raws:
        if raw.get("user_id") == user.user_id and raw.get("token_hash") not in keep:
            await store.delete_session(raw["token_hash"])
    await audit(store, "password_change", user.user_id, user.email)
    return {"ok": True}


@auth_router.post("/ws-ticket", response_model=WsTicketResponse)
async def ws_ticket(
    principal: Principal = Depends(require_scope("calls:write")),
    store: MemoryStore = Depends(get_store),
) -> dict:
    ticket = await mint_ws_ticket(store, principal)
    return {"ticket": ticket, "expires_in": auth.WS_TICKET_TTL_S}


@auth_router.get("/events", response_model=AuthEventListResponse)
async def auth_events(
    _: Principal = Depends(require_role("admin")), store: MemoryStore = Depends(get_store)
) -> AuthEventListResponse:
    return AuthEventListResponse(events=await store.list_auth_events())
