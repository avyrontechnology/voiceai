"""The pure aggregation rules behind the reported status."""

from __future__ import annotations

from voiceai.modules.health.models import ComponentHealth, HealthState
from voiceai.modules.health.static_methods import overall_status, unavailable_components


def _component(name: str, state: HealthState) -> ComponentHealth:
    """Build a component result with only the fields aggregation looks at."""
    return ComponentHealth(name=name, state=state)


def test_nothing_probed_is_up() -> None:
    """A service with no dependencies is healthy by answering at all."""
    assert overall_status([]) is HealthState.UP


def test_all_up_is_up() -> None:
    """Every dependency reachable means the service is serving."""
    components = [_component("redis", HealthState.UP), _component("database", HealthState.UP)]

    assert overall_status(components) is HealthState.UP


def test_any_down_is_down() -> None:
    """One unusable dependency takes the whole service down — the report must not average."""
    components = [_component("redis", HealthState.UP), _component("database", HealthState.DOWN)]

    assert overall_status(components) is HealthState.DOWN


def test_skipped_is_not_a_failure() -> None:
    """An unconfigured dependency is a deployment choice, not an outage."""
    components = [_component("redis", HealthState.SKIPPED), _component("database", HealthState.UP)]

    assert overall_status(components) is HealthState.UP


def test_skipped_never_rescues_a_down_component() -> None:
    """Ignoring SKIPPED must not accidentally ignore a real failure next to it."""
    components = [_component("redis", HealthState.SKIPPED), _component("database", HealthState.DOWN)]

    assert overall_status(components) is HealthState.DOWN


def test_unavailable_components_names_only_the_failures() -> None:
    """Error details name what broke, in probe order, and nothing else."""
    components = [
        _component("app", HealthState.UP),
        _component("redis", HealthState.DOWN),
        _component("database", HealthState.SKIPPED),
    ]

    assert unavailable_components(components) == ["redis"]
