"""Pure, I/O-free health computations (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.health.models import ComponentHealth, HealthState


def overall_status(components: Sequence[ComponentHealth]) -> HealthState:
    """Fold per-component states into the service-wide state.

    One ``DOWN`` component makes the service ``DOWN``; ``SKIPPED`` components are ignored
    because an unconfigured dependency is a deployment choice, not a fault. With nothing to
    probe the service is ``UP``: the process answering at all is the signal.

    Args:
        components: Probe results to fold.

    Returns:
        ``HealthState.DOWN`` when any component is down, otherwise ``HealthState.UP``.
    """
    if any(component.state is HealthState.DOWN for component in components):
        return HealthState.DOWN
    return HealthState.UP


def unavailable_components(components: Sequence[ComponentHealth]) -> list[str]:
    """List the names of the components that failed their probe.

    Args:
        components: Probe results to filter.

    Returns:
        Names of every ``DOWN`` component, in probe order, for error details and logs.
    """
    return [component.name for component in components if component.state is HealthState.DOWN]
