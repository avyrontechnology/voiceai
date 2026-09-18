"""Auth module: platform authentication + authorization, strangler tranche C (spec 0005).

``__all__`` here is the ONLY surface other code may import from this package; the
layer-contract test enforces that mechanically (AGENTS.md §3.1). The legacy
``voiceai.platform.auth`` keeps working through same-named delegators until the
cutover endgame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from voiceai.modules import ModuleDef
from voiceai.modules.auth.constants import MODULE_NAME
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
    ensure_within_attempt_limit,
)
from voiceai.modules.auth.ports import AuthStorePort

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container

#: Mounted by the app factory under the API prefix. Empty on purpose: the routes land
#: with the controller at step C5, and mounting a router with no routes changes nothing
#: observable meanwhile (the voice-B0 precedent).
router: APIRouter = APIRouter()


def register(container: Container) -> None:
    """Bind this module's providers into a container (AGENTS.md rule 9).

    No-op until the service lands at step C3: the store arrives per request through the
    legacy app.state seam, so there is nothing composition-time to bind yet.

    Args:
        container: The container being composed, already carrying the core
            infrastructure. Currently unused — kept for the ModuleDef contract.
    """


MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router, register=register)

__all__ = [
    "MODULE",
    "AuthError",
    "AuthNotFoundError",
    "AuthStorePort",
    "ForbiddenError",
    "InviteInvalidError",
    "InvalidCredentialsError",
    "TooManyAttemptsError",
    "ensure_authenticated",
    "ensure_found",
    "ensure_invite_valid",
    "ensure_permitted",
    "ensure_within_attempt_limit",
    "register",
]
