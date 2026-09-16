"""Feature-module registry: the composition data core wires into the application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:  # pragma: no cover - annotation only; modules never import core at runtime
    from voiceai.core.container import Container


@dataclass(frozen=True)
class ModuleDef:
    """Everything the core needs to mount one feature module.

    Keeping this a frozen record (no behaviour) is what lets ``core`` depend on the module
    registry without depending on any module's internals (AGENTS.md §3).

    Attributes:
        name: Stable identifier used in logs and registry lookups.
        router: Router the app factory mounts under the API prefix.
        register: Callback that binds the module's providers into a container.
    """

    name: str
    router: APIRouter
    register: Callable[[Container], None]


# Imported below the definition on purpose: each module builds its ``MODULE`` from ``ModuleDef``,
# so the class has to exist before the module packages are imported.
from voiceai.modules import agents, health  # noqa: E402

# Registration order is landing order; agents mounts an empty router until spec 0002 step A4.
ALL_MODULES: tuple[ModuleDef, ...] = (health.MODULE, agents.MODULE)

__all__ = ["ALL_MODULES", "ModuleDef"]
