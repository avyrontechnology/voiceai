"""Turn meta-info identity at the seam (spec 0033).

Drives `voiceai.modules.voice.session.turn.meta_info` directly with a fake
session — no TaskManager. Existing e2e/s2s/session suites exercise the
delegators.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from voiceai.modules.voice.session.turn import meta_info


def _session() -> SimpleNamespace:
    """Fake session: transcriber meta seed, sequence counter, turn counter."""
    transcriber = MagicMock()
    transcriber.get_meta_info.return_value = {"request_id": "r-1", "origin": "test"}
    manager = MagicMock()
    manager.get_next_sequence_id.side_effect = [101, 102, 103]
    return SimpleNamespace(
        tools={"transcriber": transcriber},
        interruption_manager=manager,
        _response_turn_id=41,
    )


def test_fresh_identity_stamps_sequence_turn_and_group() -> None:
    """A call seeds from the transcriber and stamps a fresh chain."""
    session = _session()

    stamped = meta_info.get_updated_meta_info(session, None)

    assert stamped["sequence_id"] == 101
    assert stamped["turn_id"] == 42
    assert stamped["response_uid"] == stamped["response_group_uid"]
    assert "parent_response_uid" not in stamped
    assert stamped["request_id"] == "r-1"
    assert session._response_turn_id == 42


def test_explicit_meta_copies_without_mutating() -> None:
    """The input mapping is never mutated; the copy carries new identity."""
    session = _session()
    incoming = {"request_id": "r-9", "sequence_id": 7}

    stamped = meta_info.get_updated_meta_info(session, incoming)

    assert incoming == {"request_id": "r-9", "sequence_id": 7}
    assert stamped["sequence_id"] == 101
    assert stamped["turn_id"] == 42


def test_followup_links_parent_and_drops_chunk_keys() -> None:
    """Followups chain group/parent uids and start a fresh chunk stream."""
    session = _session()
    parent = {
        "sequence_id": 101,
        "turn_id": 42,
        "response_uid": "p-uid",
        "response_group_uid": "p-group",
        "request_id": "r-1",
        "chunk_id": 3,
        "mark_id": "m",
        "is_first_chunk": True,
        "text_synthesized": "hi",
    }

    followup = meta_info.spawn_followup_meta_info(session, parent)

    assert followup["response_group_uid"] == "p-group"
    assert followup["parent_response_uid"] == "p-uid"
    assert followup["response_uid"] != "p-uid"
    for key in ("chunk_id", "mark_id", "is_first_chunk", "text_synthesized"):
        assert key not in followup
    assert parent["chunk_id"] == 3


def test_followup_without_group_falls_back_to_uid() -> None:
    """A parent without a group uid still links (uid doubles as group)."""
    session = _session()
    parent = {"sequence_id": 1, "response_uid": "solo"}

    followup = meta_info.spawn_followup_meta_info(session, parent)

    assert followup["response_group_uid"] == "solo"
    assert followup["parent_response_uid"] == "solo"
