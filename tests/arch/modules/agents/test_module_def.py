"""The agents `ModuleDef`: registered, routed, and provider-binding (spec 0002, A1 → A4).

Structural: nothing here builds the app or touches infrastructure. A1 landed these as
"empty router / no-op register until A4"; A4 rewrote those two pins in place (reconciled in
spec 0002 §Verification) — the router now mounts the four legacy paths and `register` binds
the repository, the service, and the ports (rule 9).
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from voiceai.modules import ALL_MODULES, ModuleDef, agents
from voiceai.modules.agents.constants import (
    AGENT_BY_ID_PATH,
    AGENT_PATH,
    AGENT_PROMPTS_PATH,
    ALL_AGENTS_PATH,
    ASSISTANT_STATUS_SEEDING,
    ASSISTANT_STATUS_UPDATED,
    MODULE_NAME,
    PREPROCESS_DIR,
)
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort
from voiceai.modules.agents.repository import FilePromptStore
from voiceai.modules.agents.service import AgentService

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


def test_router_mounts_exactly_the_four_legacy_paths():
    """A4's controller serves the quickstart surface under the API prefix — no more, no less."""
    from fastapi.routing import APIRoute

    paths = {route.path for route in agents.MODULE.router.routes if isinstance(route, APIRoute)}

    assert len(agents.MODULE.router.routes) == 6  # GET+PUT+DELETE on /agent/{id} share a path
    assert paths == {AGENT_PATH, AGENT_BY_ID_PATH, AGENT_PROMPTS_PATH, ALL_AGENTS_PATH}


def test_register_binds_the_repository_service_and_ports():
    """A4's `register` binds every composition key spec 0002 names (rule 9), via a spy."""
    keys: list[object] = []
    spy = SimpleNamespace(register=lambda key, provider, **kwargs: keys.append(key))

    agents.MODULE.register(cast("Container", spy))

    assert set(keys) == {
        agents.CONTAINER_KEY_AGENT_DEFINITIONS,
        FilePromptStore,
        AgentService,
        AgentDefinitionPort,
        AgentSessionStorePort,
    }


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
