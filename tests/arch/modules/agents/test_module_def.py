"""The agents `ModuleDef`: registered, empty router, no-op register (spec 0002, step A1).

Structural and import-only: nothing here builds the app or touches infrastructure. The
scaffold's contract is exactly that it changes nothing observable until step A4.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from voiceai.modules import ALL_MODULES, ModuleDef, agents
from voiceai.modules.agents.constants import (
    ASSISTANT_STATUS_SEEDING,
    ASSISTANT_STATUS_UPDATED,
    MODULE_NAME,
    PREPROCESS_DIR,
)

if TYPE_CHECKING:  # annotation only: the spy stands in for a container at runtime
    from voiceai.core.container import Container


def test_module_is_a_frozen_module_def_named_agents():
    """The registry entry is composition data with the module's own name."""
    assert isinstance(agents.MODULE, ModuleDef)
    assert agents.MODULE.name == MODULE_NAME

    with pytest.raises(dataclasses.FrozenInstanceError):
        agents.MODULE.name = "renamed"  # type: ignore[misc]  # the point: assignment must raise


def test_agents_module_is_registered_in_all_modules():
    """Spec 0001 registry pattern: the module rides `ALL_MODULES` from day one (step A1)."""
    assert agents.MODULE in ALL_MODULES


def test_router_mounts_no_routes_yet():
    """An empty router changes nothing observable; the controller arrives in step A4."""
    assert agents.MODULE.router.routes == []


def test_register_is_a_noop_until_a4():
    """`register` binds nothing: a recording spy sees zero registration calls."""
    calls: list[object] = []
    spy = SimpleNamespace(register=lambda *args, **kwargs: calls.append((args, kwargs)))

    agents.MODULE.register(cast("Container", spy))

    assert calls == []


def test_public_surface_exports_the_ports():
    """`voice` may import only `__all__` names (§3.1 bridge 4); the ports must be on it."""
    assert "AgentDefinitionPort" in agents.__all__
    assert "AgentSessionStorePort" in agents.__all__
    assert "MODULE" in agents.__all__


def test_constants_pin_the_legacy_literals():
    """The scaffold's literals must match the legacy values they will replace (spec 0002)."""
    import voiceai.constants as legacy_constants

    assert PREPROCESS_DIR == legacy_constants.PREPROCESS_DIR
    assert ASSISTANT_STATUS_SEEDING == "seeding"
    assert ASSISTANT_STATUS_UPDATED == "updated"
