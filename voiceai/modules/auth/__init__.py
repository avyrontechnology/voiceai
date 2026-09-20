"""Auth module: platform authentication + authorization, strangler tranche C (spec 0005).

``__all__`` here is the ONLY surface other code may import from this package; the
layer-contract test enforces that mechanically (AGENTS.md §3.1). The legacy
``voiceai.platform.auth`` keeps working through same-named delegators until the
cutover endgame.
"""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.auth.constants import MODULE_NAME
from voiceai.modules.auth.controller import router
from voiceai.modules.auth.errors import (
    AuthError,
    AuthNotFoundError,
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
    TooManyAttemptsError,
)
from voiceai.modules.auth.exceptions import (
    ensure_authenticated,
    ensure_found,
    ensure_invite_valid,
    ensure_permitted,
)
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.service import AuthService

MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router)

__all__ = [
    "MODULE",
    "AuthError",
    "AuthNotFoundError",
    "AuthService",
    "AuthStorePort",
    "ForbiddenError",
    "InviteInvalidError",
    "InvalidCredentialsError",
    "TooManyAttemptsError",
    "ensure_authenticated",
    "ensure_found",
    "ensure_invite_valid",
    "ensure_permitted",
]
