"""Small formatting helpers for the health module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.health.constants import SUMMARY_ITEM_TEMPLATE, SUMMARY_SEPARATOR
from voiceai.modules.health.models import ComponentHealth


def component_summary(components: Sequence[ComponentHealth]) -> str:
    """Render component states as one grep-friendly log fragment.

    Args:
        components: Probe results to render.

    Returns:
        A single line such as ``"app=up redis=up database=up"``; empty when nothing was probed.
    """
    return SUMMARY_SEPARATOR.join(
        SUMMARY_ITEM_TEMPLATE.format(name=component.name, state=component.state.value) for component in components
    )
