"""Module-internal impure utilities: the per-process login limiter (spec 0005, C3).

Moved VERBATIM from ``voiceai/platform/auth.py`` (module-global attempt ledger plus
the sliding-window check); only the raised type changes — ``TooManyAttemptsError``
instead of the legacy ``HTTPException`` 429, since only the controller speaks HTTP.
Documented limit, unchanged: 5 attempts per IP per minute, per process. Behind
multiple workers use a shared limiter — this local one is per-process.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from voiceai.modules.auth.constants import LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S
from voiceai.modules.auth.errors import TooManyAttemptsError

__all__ = [
    "check_login_allowed",
    "try_login_attempt",
]

_attempts: dict[str, deque[float]] = defaultdict(deque)


def try_login_attempt(ip: str) -> bool:
    """Record one login attempt, reporting whether the IP stays within its window.

    Args:
        ip: Client address (already extracted from headers or connection info).

    Returns:
        ``True`` when the attempt is allowed (and now counted); ``False`` when the
        IP exhausted its window.
    """
    now = time.time()
    window = _attempts[ip]
    while window and now - window[0] > LOGIN_WINDOW_S:
        window.popleft()
    if len(window) >= LOGIN_MAX_ATTEMPTS:
        return False
    window.append(now)
    return True


def check_login_allowed(ip: str) -> None:
    """Record one login attempt, rejecting an exhausted window.

    Args:
        ip: Client address (already extracted from headers or connection info).

    Raises:
        TooManyAttemptsError: When the IP exhausted its window.
    """
    if not try_login_attempt(ip):
        raise TooManyAttemptsError("Too many login attempts, try again shortly")
