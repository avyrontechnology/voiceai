"""The module registry `core` composes the application from."""

from __future__ import annotations

import dataclasses

import pytest

from voiceai.modules import ALL_MODULES, ModuleDef, agents, auth, catalog, chat, health, tools, voice, voices, wallet


def test_registry_lists_exactly_the_registered_modules() -> None:
    """Core mounts what this tuple says: health, agents, voice (spec 0004 B0), auth (spec 0005 C5), wallet, catalog (spec 0022), voices (spec 0025), tools (spec 0029), chat (spec 0038)."""
    assert ALL_MODULES == (
        health.MODULE,
        agents.MODULE,
        voice.MODULE,
        auth.MODULE,
        wallet.MODULE,
        catalog.MODULE,
        voices.MODULE,
        tools.MODULE,
        chat.MODULE,
    )
    assert all(isinstance(module, ModuleDef) for module in ALL_MODULES)


def test_module_def_is_frozen() -> None:
    """A registry entry is composition data; nothing may rewrite it after import."""
    module = ALL_MODULES[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        module.name = "renamed"  # type: ignore[misc]  # the point: assignment must raise


def test_registry_entries_carry_ownership() -> None:
    """Every entry names its squad and runbook (spec 0010, docs/OWNERSHIP.md mirrors this)."""
    for module in ALL_MODULES:
        assert module.owner_squad, f"{module.name}: owner_squad is empty"
        assert module.runbook_path, f"{module.name}: runbook_path is empty"
        assert module.runbook_path.startswith("voiceai/modules/"), module.runbook_path


def test_registry_entries_carry_positive_line_budget() -> None:
    """Every entry declares a hard line ceiling (spec 0019, test_size_budgets.py enforces it)."""
    for module in ALL_MODULES:
        assert module.max_lines > 0, f"{module.name}: max_lines is not set"
