"""Authentication + authorization core for the platform layer.

Self-hosted email/password with bcrypt-grade hashing (PBKDF2-SHA256 from the
standard library — no new dependencies), opaque server-side sessions in Redis
(with TTL), and scoped Bearer API keys. Roles reuse the existing
owner/admin/member/viewer ladder.

Notes for operators:
- Passwords, session tokens, invite tokens and API secrets are stored only
  as hashes (PBKDF2 / SHA-256). Nothing credential-equivalent hits disk.
- Login attempts are throttled per IP in-process (5/min). Behind multiple
  workers use a shared limiter — this local one is per-process.
- Sessions live server-side, so logout and role/disable changes take effect
  immediately; there is nothing client-side to revoke.
"""

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, Response

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import ROLE_RANK, ROLE_SCOPES, SessionRecord, User, new_id, utcnow
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

SESSION_COOKIE = "otoba_session"
SESSION_TTL_S = 7 * 24 * 3600
REMEMBER_TTL_S = 30 * 24 * 3600
WS_TICKET_TTL_S = 60
INVITE_TTL_S = 7 * 24 * 3600
LOGIN_WINDOW_S = 60
LOGIN_MAX_ATTEMPTS = 5
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")

_PBKDF2_ITERATIONS = 600_000


def get_store(request: Request) -> MemoryStore:
    """Store accessor that avoids a router import cycle."""
    store = getattr(request.app.state, "platform_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Platform store unavailable")
    return store


# -- password + token hashing ----------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, reference: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = reference.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# -- principal ---------------------------------------------------------------------

@dataclass
class Principal:
    user_id: Optional[str]
    email: Optional[str]
    org_id: str = "default"
    role: str = "viewer"
    auth_type: str = "session"  # "session" | "key"
    scopes: List[str] = field(default_factory=list)
    key_id: Optional[str] = None
    key_name: Optional[str] = None

    def effective_scopes(self) -> List[str]:
        if self.auth_type == "key":
            return self.scopes
        return ROLE_SCOPES.get(self.role, [])

    def has_scope(self, scope: str) -> bool:
        scopes = self.effective_scopes()
        return "*" in scopes or scope in scopes

    def has_role(self, minimum: str) -> bool:
        if self.auth_type == "key":
            return "*" in self.scopes
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK.get(minimum, 99)


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


def _forbidden(detail: str = "Insufficient permissions") -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


async def _principal_from_session(
    store: MemoryStore, token: str
) -> Optional[Principal]:
    session = await store.get_session(token_hash(token))
    if not session or session.kind != "session":
        return None
    user = await store.get_user(session.user_id)
    if not user or user.disabled:
        return None
    return Principal(
        user_id=user.user_id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        auth_type="session",
    )


async def _principal_from_api_key(store: MemoryStore, secret: str) -> Optional[Principal]:
    digest = token_hash(secret)
    for key in await store.list_api_keys():
        if not key.key_hash:
            continue  # legacy key minted before hashing; rotate it
        prefix_ok = secret.startswith(key.prefix) if key.prefix else True
        if prefix_ok and hmac.compare_digest(key.key_hash, digest):
            if key.expires_at and key.expires_at.replace(tzinfo=timezone.utc) < utcnow():
                return None
            key.last_used_at = utcnow()
            try:
                await store.save_api_key(key)
            except Exception:
                logger.warning(f"Could not touch last_used_at for key {key.key_id}")
            user = await store.get_user(key.created_by) if key.created_by else None
            return Principal(
                user_id=key.created_by,
                email=user.email if user else None,
                org_id=user.org_id if user else "default",
                role="viewer",
                auth_type="key",
                scopes=list(key.scopes),
                key_id=key.key_id,
                key_name=key.name,
            )
    return None


async def get_principal(request: Request, store: MemoryStore = Depends(get_store)) -> Principal:
    """Resolve the caller from session cookie, else Bearer API key. 401 if neither."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        principal = await _principal_from_session(store, token)
        if principal:
            return principal
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        principal = await _principal_from_api_key(store, authorization[7:].strip())
        if principal:
            return principal
    raise _unauthorized()


async def require_principal(principal: Principal = Depends(get_principal)) -> Principal:
    return principal


def require_role(minimum: str):
    async def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_role(minimum):
            raise _forbidden(f"Requires {minimum} role or higher")
        return principal

    return dep


def require_scope(scope: str):
    async def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_scope(scope):
            raise _forbidden(f"Requires {scope} scope")
        return principal

    return dep


# -- sessions / cookies --------------------------------------------------------------

async def mint_session(
    store: MemoryStore, user: User, response: Response, ttl_s: int = SESSION_TTL_S
) -> str:
    token = new_token()
    await store.save_session(
        SessionRecord(
            token_hash=token_hash(token),
            user_id=user.user_id,
            org_id=user.org_id,
            kind="session",
            expires_at=utcnow() + timedelta(seconds=ttl_s),
        )
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl_s,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]
        path="/",
    )
    return token


async def revoke_session(store: MemoryStore, request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await store.delete_session(token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")


async def mint_ws_ticket(store: MemoryStore, principal: Principal) -> str:
    """Single-use 60s ticket for the voice websocket (browsers can't set WS headers)."""
    ticket = new_token()
    await store.save_session(
        SessionRecord(
            token_hash=token_hash(ticket),
            user_id=principal.user_id or "",
            org_id=principal.org_id,
            kind="ws-ticket",
            expires_at=utcnow() + timedelta(seconds=WS_TICKET_TTL_S),
        )
    )
    return ticket


async def redeem_ws_ticket(store: MemoryStore, ticket: str) -> Optional[Principal]:
    session = await store.get_session(token_hash(ticket))
    if not session or session.kind != "ws-ticket":
        return None
    await store.delete_session(session.token_hash)
    if not session.user_id:
        return None
    user = await store.get_user(session.user_id)
    if not user or user.disabled:
        return None
    return Principal(
        user_id=user.user_id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        auth_type="session",
    )


# -- login throttling ------------------------------------------------------------------

_attempts: Dict[str, Deque[float]] = defaultdict(deque)


def check_login_allowed(ip: str) -> None:
    now = time.time()
    window = _attempts[ip]
    while window and now - window[0] > LOGIN_WINDOW_S:
        window.popleft()
    if len(window) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts, try again shortly")
    window.append(now)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# -- audit -------------------------------------------------------------------------------

async def audit(
    store: MemoryStore,
    event_type: str,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    from voiceai.platform.models import AuthEvent

    try:
        await store.add_auth_event(
            AuthEvent(event_id=new_id("evt"), type=event_type, user_id=user_id, email=email, detail=detail)
        )
    except Exception:
        logger.warning(f"Auth audit write failed for {event_type}")


def public_user(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "org_id": user.org_id,
        "disabled": user.disabled,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }
