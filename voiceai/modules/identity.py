# legacy-shim(spec-0020): `auth` is becoming `identity`; import the new name.
"""The identity surface, re-exported from the auth module until the M9 cutover.

`auth → identity` is a rename, not a fork: every name here is the auth
package's public surface, so new code imports ``voiceai.modules.identity``
while the implementation still lives under ``modules/auth``. A single-file
module (not a package) on purpose: the module-shape gate treats every
``modules/*/`` directory as a feature module, and this is a name, not one.
"""

from __future__ import annotations

from voiceai.modules.auth import (
    MODULE,
    SESSION_COOKIE,
    AuthError,
    AuthNotFoundError,
    AuthService,
    AuthStorePort,
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
    Principal,
    TooManyAttemptsError,
    ensure_authenticated,
    ensure_found,
    ensure_invite_valid,
    ensure_permitted,
    request_principal,
)

__all__ = [
    "MODULE",
    "AuthError",
    "AuthNotFoundError",
    "AuthService",
    "AuthStorePort",
    "ForbiddenError",
    "InviteInvalidError",
    "InvalidCredentialsError",
    "Principal",
    "SESSION_COOKIE",
    "TooManyAttemptsError",
    "ensure_authenticated",
    "ensure_found",
    "ensure_invite_valid",
    "ensure_permitted",
    "request_principal",
]
