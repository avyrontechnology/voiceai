"""The moved interruption manager (spec 0004, B10): identity shim + behavior at the new home.

Two contracts under test, the B5 ``test_s2s_runner`` precedent. First, the
``InterruptionManager`` class behaves concretely at its NEW home
(``voiceai.modules.voice.session.interruption``): the audio gate (SEND/BLOCK/WAIT),
the interruption gates, sequence management and stats. Second, every legacy
``voiceai.agent_manager.interruption_manager`` path is a pure identity shim over
the relocated class.
"""

from voiceai.agent_manager.interruption_manager import InterruptionManager as LegacyManager
from voiceai.modules.voice.session.interruption import InterruptionManager as NewManager


def test_legacy_path_is_an_identity_shim_over_the_relocated_class():
    assert LegacyManager is NewManager


def test_audio_gate_blocks_invalid_sequences():
    manager = NewManager()
    assert manager.get_audio_send_status(999) == "BLOCK"


def test_audio_gate_waits_while_user_speaks():
    manager = NewManager()
    seq = manager.get_next_sequence_id()
    manager.on_user_speech_started()
    assert manager.get_audio_send_status(seq) == "WAIT"


def test_audio_gate_sends_for_a_fresh_sequence():
    manager = NewManager()
    seq = manager.get_next_sequence_id()
    assert manager.get_audio_send_status(seq) == "SEND"


def test_interruption_gate_needs_audio_and_welcome():
    manager = NewManager(number_of_words_for_interruption=3)
    assert manager.should_trigger_interruption(10, "hello world this is long", False, True) is False
    assert manager.should_trigger_interruption(10, "hello world this is long", True, False) is False
    assert manager.should_trigger_interruption(10, "hello world this is a long sentence", True, True) is True
    assert manager.should_trigger_interruption(1, "hi", True, True) is False


def test_sequence_lifecycle_retires_and_invalidates():
    manager = NewManager()
    seq = manager.get_next_sequence_id()
    assert manager.is_valid_sequence(seq) is True
    manager.retire_sequence_id(seq)
    assert manager.is_valid_sequence(seq) is False
    manager.invalidate_pending_responses()
    assert manager.has_pending_responses() is False
    assert manager.has_pending_responses_excluding(None) is False


def test_barge_in_recovery_is_counted_once():
    manager = NewManager()
    manager.on_interruption_triggered()
    assert manager.user_interrupted_agent_count == 1
    manager.on_successful_response_delivered()
    assert manager.barge_in_recovery_count == 1
    stats = manager.get_interruption_stats(call_start_ms=0.0)
    assert stats["user_interrupted_agent_count"] == 1
    assert stats["barge_in_recovery_count"] == 1
    assert stats["barge_in_recovery_rate"] == 100.0
