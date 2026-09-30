"""Authentication + authorization core for the platform layer.

Principal resolution from server-side sessions and scoped Bearer API keys, read
through the platform store (spec 0048: the greenfield auth store underneath).
Roles reuse the existing owner/admin/member/viewer ladder.

Spec 0006 E4: session-minting and login-throttle delegators retired to
``voiceai.modules.auth`` (service, utils, helpers, constants). What remains is
the principal-resolution chain the frozen platform routers (``platform/router.py``)
still depend on — ``get_store`` / ``get_principal`` / ``require_*`` / ``audit`` —
plus the credential-primitive re-exports other importers patch against.

Notes for operators:
- Passwords, session tokens, invite tokens and API secrets are stored only
  as hashes (PBKDF2 / SHA-256). Nothing credential-equivalent hits disk.
- Sessions live server-side, so logout and role/disable changes take effect
  immediately; there is nothing client-side to revoke.
"""

from __future__ import annotations

import hmac
from datetime import timezone
from typing import Optional, TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from voiceai.common.errors import ConfigurationError
from voiceai.core.app_factory import platform_store_of
from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import new_id, utcnow

if TYPE_CHECKING:
    from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

SESSION_COOKIE = "otoba_session"


def get_store(request: Request) -> MemoryStore:
    """Store accessor that avoids a router import cycle (staged store, else the container's)."""
    try:
        return platform_store_of(request.app)
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Platform store unavailable") from exc


# -- password + token hashing ----------------------------------------------------

# spec-0005 C1: the pure credential primitives live VERBATIM in
# voiceai.modules.auth.static_methods. These same-named bindings keep THIS module the
# lookup/patch site (`voiceai.platform.auth.<name>` keeps resolving for imports AND for
# monkeypatch string paths). The `as` spelling makes each binding an EXPLICIT re-export.
from voiceai.modules.auth.static_methods import hash_password as hash_password
from voiceai.modules.auth.static_methods import new_token as new_token
from voiceai.modules.auth.static_methods import token_hash as token_hash
from voiceai.modules.auth.static_methods import verify_password as verify_password


# -- principal ---------------------------------------------------------------------
# spec-0005 C2: Principal lives VERBATIM in voiceai.modules.auth.models.principal.
# Same-named binding keeps THIS module the lookup/patch site; the `as` spelling makes
# it an EXPLICIT re-export.
from voiceai.modules.auth.models.principal import Principal as Principal


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


def _forbidden(detail: str = "Insufficient permissions") -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


async def _principal_from_session(store: MemoryStore, token: str) -> Optional[Principal]:
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
    """Resolve a Bearer secret by its hash: one indexed read, never a key scan (spec 0048).

    A key minted before hashing (no ``key_hash``) can never match and must be rotated;
    the prefix and expiry checks are the pre-cutover ones, verbatim.
    """
    digest = token_hash(secret)
    key = await store.get_api_key_by_hash(digest)
    if key is None or not key.key_hash or not hmac.compare_digest(key.key_hash, digest):
        return None
    if key.prefix and not secret.startswith(key.prefix):
        return None
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


async def get_principal(request: Request, store: MemoryStore = Depends(get_store)) -> Principal:
    """Resolve the caller from session cookie, else Bearer API key. 401 if neither.

    Both paths are single indexed reads on the platform store, which in the single
    app persists through the greenfield auth store (spec 0048): no scan, no Redis.
    """
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
