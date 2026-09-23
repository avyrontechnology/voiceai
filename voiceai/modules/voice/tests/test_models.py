"""`ComponentLatencies` after its B3 move: shape frozen, shim aliased, engine intact.

The class moved verbatim from ``voiceai/agent_manager/models.py`` into the voice
module's models file; the legacy path became a pure re-export shim. Three things must
hold forever after: the persisted field shape cannot drift (the teardown snapshot dumps
it into ``latency_dict``), the shim and the moved class are ONE object, and
``task_manager`` — whose ``from .models import ComponentLatencies`` now rides the shim —
still builds its three per-component instances from that same object.
"""

from __future__ import annotations

import voiceai.agent_manager.models as legacy_models
import voiceai.agent_manager.task_manager as legacy_tm
from voiceai.modules.voice.models import ComponentLatencies

#: The persisted field set of the teardown snapshot's component payloads (frozen).
COMPONENT_LATENCY_FIELDS = frozenset({"connection_latency_ms", "turn_latencies", "other_latencies"})


def test_field_set_and_defaults_are_the_legacy_shape():
    """Exactly the three legacy fields, defaulting to None and two empty lists."""
    latencies = ComponentLatencies()

    assert frozenset(ComponentLatencies.model_fields) == COMPONENT_LATENCY_FIELDS
    assert latencies.connection_latency_ms is None
    assert latencies.turn_latencies == []
    assert latencies.other_latencies == []


def test_model_dump_matches_the_teardown_snapshot_shape():
    """The dict task_manager persists per component keeps its exact key set."""
    dump = ComponentLatencies().model_dump()

    assert dump == {"connection_latency_ms": None, "turn_latencies": [], "other_latencies": []}


def test_default_lists_are_per_instance():
    """`default_factory` semantics survived the move: no shared mutable defaults."""
    first = ComponentLatencies()
    second = ComponentLatencies()
    first.turn_latencies.append({"sequence_id": 1})

    assert second.turn_latencies == []


def test_legacy_shim_and_task_manager_alias_the_moved_class():
    """One class object across all three import paths (identity, never a duplicate)."""
    assert legacy_models.ComponentLatencies is ComponentLatencies
    assert legacy_tm.ComponentLatencies is ComponentLatencies
