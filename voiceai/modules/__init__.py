"""Feature-module registry: the composition data core wires into the application."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class ModuleDef:
    """Everything the core needs to mount one feature module.

    Keeping this a frozen record (no behaviour) is what lets ``core`` depend on the module
    registry without depending on any module's internals (AGENTS.md §3).

    Attributes:
        name: Stable identifier used in logs and registry lookups.
        router: Router the app factory mounts under the API prefix.
    """

    name: str
    router: APIRouter


# Imported below the definition on purpose: each module builds its ``MODULE`` from ``ModuleDef``,
# so the class has to exist before the module packages are imported.
from voiceai.modules import agents, auth, health, voice, wallet  # noqa: E402

# Registration order is landing order; voice mounts an empty router until spec 0004 step B14.
ALL_MODULES: tuple[ModuleDef, ...] = (health.MODULE, agents.MODULE, voice.MODULE, auth.MODULE, wallet.MODULE)

__all__ = ["ALL_MODULES", "ModuleDef"]
