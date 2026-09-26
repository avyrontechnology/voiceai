"""Settings truth pass schema tests (spec 0042, Slice A; integrator-closed).

Offline: every test builds :class:`ConversationConfig` in memory. The cases pin
the Slice A contract — the proven-dead key is gone, ``recording`` defaults
to off with an explicit flag surviving a dump round-trip, the wired
``interruption_backoff_period`` defaults to 0 (hold disabled), and the
promoted hidden keys validate with their identical runtime meaning.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.modules.agents.models import ConversationConfig


def test_dead_keys_absent_from_schema() -> None:
    """The proven-unread key no longer ships on the validated write path."""
    assert "ambient_noise" not in ConversationConfig.model_fields


def test_dead_keys_ignored_not_stored() -> None:
    """Stale payloads carrying the deleted key validate without persisting it."""
    cfg = ConversationConfig.model_validate({"ambient_noise": True})

    assert "ambient_noise" not in cfg.model_dump()


def test_backoff_defaults_to_disabled_hold() -> None:
    """The Slice B-wired hold defaults to 0 (integrator-restored after the delete-default)."""
    assert ConversationConfig().interruption_backoff_period == 0


def test_recording_defaults_off() -> None:
    """Capture stays off unless a writer opts in explicitly."""
    assert ConversationConfig().recording is False


def test_promoted_keys_default_absent() -> None:
    """Promoted hidden keys default to absent, preserving legacy derivation."""
    cfg = ConversationConfig()

    assert cfg.call_hangup_message is None
    assert cfg.welcome_message_delay is None


def test_recording_rejects_non_boolean() -> None:
    """The capture flag is a strict toggle, not a truthy string."""
    with pytest.raises(ValidationError):
        ConversationConfig(recording="definitely")  # type: ignore[arg-type]


def test_call_hangup_message_rejects_non_stringish() -> None:
    """The hangup message accepts the live str-or-language-dict shapes only."""
    with pytest.raises(ValidationError):
        ConversationConfig(call_hangup_message=["bye"])  # type: ignore[arg-type]


def test_welcome_message_delay_rejects_non_numeric() -> None:
    """The welcome delay accepts numeric values only."""
    with pytest.raises(ValidationError):
        ConversationConfig(welcome_message_delay="soon")  # type: ignore[arg-type]


def test_round_trip_preserves_explicit_values() -> None:
    """Construct, dump, and revalidate: explicit flags survive byte-identical."""
    cfg = ConversationConfig(
        recording=True,
        call_hangup_message="Thanks for calling, goodbye.",
        welcome_message_delay=1.5,
        hangup_after_silence=20,
        use_fillers=True,
    )

    assert ConversationConfig.model_validate(cfg.model_dump()) == cfg


def test_round_trip_preserves_language_dict_hangup_message() -> None:
    """The per-language dict shape (live in the runtime) survives the round-trip."""
    cfg = ConversationConfig(call_hangup_message={"en": "Goodbye.", "hi": "Alvida."})

    revived = ConversationConfig.model_validate(cfg.model_dump())

    assert revived.call_hangup_message == {"en": "Goodbye.", "hi": "Alvida."}


def test_explicit_absent_markers_not_synced_to_defaults() -> None:
    """Explicit None on the promoted keys stays None (no validator rewrites it)."""
    cfg = ConversationConfig(call_hangup_message=None, welcome_message_delay=None)

    revived = ConversationConfig.model_validate(cfg.model_dump())

    assert revived.call_hangup_message is None
    assert revived.welcome_message_delay is None
