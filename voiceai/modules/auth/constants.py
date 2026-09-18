"""Every literal the auth module uses (AGENTS.md rule 1b)."""

from __future__ import annotations

from typing import Final

#: Module name for the logger, router tags and registry entry.
MODULE_NAME: Final[str] = "auth"

#: Session cookie carrying the opaque session token.
SESSION_COOKIE: Final[str] = "otoba_session"

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

#: PBKDF2 iterations for password hashing (frozen by spec 0005 non-goals).
PBKDF2_ITERATIONS: Final[int] = 600_000
