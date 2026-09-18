"""Auth module: platform authentication + authorization, strangler tranche C (spec 0005).

``__all__`` here is the ONLY surface other code may import from this package; the
layer-contract test enforces that mechanically (AGENTS.md §3.1). The legacy
``voiceai.platform.auth`` keeps working through same-named delegators until the
cutover endgame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container


def register(container: Container) -> None:
    """Bind this module's store port into the container (AGENTS.md rule 9; spec 0006 E1).

    The provider is core's strangler factory: the environment picks `MemoryStore` (no
    redis — tests, single-proc dev) or `RedisStore` over the container client
    (deployed). The legacy import lives inside that factory, deferred to first resolve,
    so this file keeps zero legacy imports and the layer-contract test stays green;
    spec 0006 E4 retires the bridge. Re-affirms the binding `build_container` already
    installed, so composing this module standalone resolves identically.

    Args:
        container: The container being composed, already carrying the core
            infrastructure.
    """
    from voiceai.core.container import create_auth_store  # deferred: core owns the strangler bridge until E4

    # The port class object is the key: abstract for mypy, hashable at runtime.
    container.register(AuthStorePort, create_auth_store)  # type: ignore[type-abstract]


MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router, register=register)

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
    "register",
]
