"""Feature-module registry: the composition data core wires into the application."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class ModuleDef:
    """Everything the core needs to mount one feature module.

    Keeping this a frozen record (no behaviour) is what lets ``core`` depend on the module
    registry without depending on any module's internals (AGENTS.md §3).

    Ownership is registry data (spec 0000, team decision: no CODEOWNERS files):
    every entry names its squad, contact channel, and runbook so 100 engineers
    can find the owner without tribal knowledge.

    Attributes:
        name: Stable identifier used in logs and registry lookups.
        router: Router the app factory mounts under the API prefix.
        owner_squad: Owning squad, e.g. ``squad-voice``.
        slack_channel: Contact channel, e.g. ``#squad-voice``.
        runbook_path: Repo-relative path to the module runbook.
        max_lines: Hard ceiling on total Python lines under the module dir
            (spec 0019, enforced by test_size_budgets.py; only ratchets down).
    """

    name: str
    router: APIRouter
    owner_squad: str = ""
    slack_channel: str = ""
    runbook_path: str = ""
    max_lines: int = 0


# Imported below the definition on purpose: each module builds its ``MODULE`` from ``ModuleDef``,
# so the class has to exist before the module packages are imported.
from voiceai.modules import agents, auth, catalog, health, tools, voice, voices, wallet  # noqa: E402

# Registration order is landing order; voice mounts an empty router until spec 0004 step B14.
ALL_MODULES: tuple[ModuleDef, ...] = (
    health.MODULE,
    agents.MODULE,
    voice.MODULE,
    auth.MODULE,
    wallet.MODULE,
    catalog.MODULE,
    voices.MODULE,
    tools.MODULE,
)

__all__ = ["ALL_MODULES", "ModuleDef"]
