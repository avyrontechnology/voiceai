"""Every literal the auth module uses (AGENTS.md rule 1b)."""

from __future__ import annotations

from typing import Final, Literal

#: Module name for the logger, router tags and registry entry.
MODULE_NAME: Final[str] = "auth"

#: Session cookie carrying the opaque session token.
SESSION_COOKIE: Final[str] = "otoba_session"

#: Refresh cookie carrying the opaque JWT refresh token (httpOnly, T2).
REFRESH_COOKIE: Final[str] = "otoba_refresh"

#: Session-record kinds on the shared session ledger.
SESSION_KIND: Final[Literal["session"]] = "session"
WS_TICKET_KIND: Final[Literal["ws-ticket"]] = "ws-ticket"

#: Default session lifetime (7 days, seconds).
SESSION_TTL_S: Final[int] = 7 * 24 * 3600

#: Remember-me session lifetime (30 days, seconds).
REMEMBER_TTL_S: Final[int] = 30 * 24 * 3600

#: Single-use websocket ticket lifetime (60 seconds).
WS_TICKET_TTL_S: Final[int] = 60

#: Invite token lifetime (7 days, seconds).
INVITE_TTL_S: Final[int] = 7 * 24 * 3600

#: Sliding window for the per-IP login throttle (seconds).
LOGIN_WINDOW_S: Final[int] = 60

#: Max login attempts per IP inside the window.
LOGIN_MAX_ATTEMPTS: Final[int] = 5

#: Redis key prefix for the shared per-IP login-throttle counters (spec 0006, E3).
#: Full keys read ``auth:throttle:{ip}``; values are ephemeral counters with a
#: ``LOGIN_WINDOW_S`` TTL, not persisted models (no `Collections` entry).
THROTTLE_KEY_PREFIX: Final[str] = "auth:throttle:"

#: Redis key prefix for the JWT denylist cache (T2): ``auth:denied:{jti}`` holds
#: ``"1"`` with the access token's remaining TTL. Ephemeral by design — the
#: `revoked_tokens` collection is the truth, the cache is speed.
DENYLIST_KEY_PREFIX: Final[str] = "auth:denied:"

#: Authorization scheme for JWT access tokens on the wire.
BEARER_SCHEME: Final[str] = "bearer"

#: PBKDF2 iterations for password hashing (frozen by spec 0005 non-goals).
PBKDF2_ITERATIONS: Final[int] = 600_000

#: Mutation-ack body the legacy routes return (copied per response, never shared).
OK_BODY: Final[dict[str, bool]] = {"ok": True}
