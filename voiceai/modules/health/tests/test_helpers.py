"""Formatting helpers: the grep-friendly component summary."""

from __future__ import annotations

from voiceai.modules.health.helpers import component_summary
from voiceai.modules.health.models import ComponentHealth, HealthState


def _component(name: str, state: HealthState) -> ComponentHealth:
    """Build a component result with only the fields the summary renders."""
    return ComponentHealth(name=name, state=state)


def test_component_summary_renders_space_separated_name_state_pairs() -> None:
    """The summary is exactly the `name=state` fragment the report log greps for."""
    components = [
        _component("app", HealthState.UP),
        _component("redis", HealthState.UP),
        _component("database", HealthState.UP),
    ]

    assert component_summary(components) == "app=up redis=up database=up"


def test_component_summary_spells_every_state_by_its_wire_value() -> None:
    """Each state renders as its enum value, in probe order, never the Python repr."""
    components = [
        _component("redis", HealthState.DOWN),
        _component("database", HealthState.SKIPPED),
    ]

    assert component_summary(components) == "redis=down database=skipped"


def test_component_summary_of_nothing_is_an_empty_string() -> None:
    """Probing nothing renders as an empty fragment, not a crash or a literal `None`."""
    assert component_summary([]) == ""
