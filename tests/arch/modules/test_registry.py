"""The module registry `core` composes the application from."""

from __future__ import annotations

import dataclasses

import pytest

from voiceai.modules import ALL_MODULES, ModuleDef
from voiceai.modules.health import MODULE


def test_registry_lists_exactly_the_health_module() -> None:
    """Core mounts what this tuple says: today that is the health module, alone and in order."""
    assert ALL_MODULES == (MODULE,)
    assert all(isinstance(module, ModuleDef) for module in ALL_MODULES)


def test_module_def_is_frozen() -> None:
    """A registry entry is composition data; nothing may rewrite it after import."""
    module = ALL_MODULES[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        module.name = "renamed"  # type: ignore[misc]  # the point: assignment must raise
