"""Unit tests for ConversationHistory.replace_last_user — used by the language-switch
path to swap the garbled locked-pool transcript for the unbiased detector transcript.
"""

from voiceai.helpers.conversation_history import ConversationHistory
from voiceai.enums import ChatRole


def _history():
    h = ConversationHistory()
    h.setup_system_prompt({"role": ChatRole.SYSTEM, "content": "prompt"})
    h.append_user("चल रही है")
    return h


def test_replaces_matching_last_user():
    h = _history()
    assert h.replace_last_user("चल रही है", "நீங்க என்ன சொல்றீங்க?") is True
    assert h.messages[-1]["content"] == "நீங்க என்ன சொல்றீங்க?"


def test_finds_user_behind_assistant_reply():
    # By switch time the heard assistant reply is already materialized after the user turn.
    h = _history()
    h.append_assistant("generic reply", turn_id=3)
    assert h.replace_last_user("चल रही है", "corrected") is True
    assert h.messages[-2]["content"] == "corrected"
    assert h.messages[-1]["content"] == "generic reply"


def test_skips_when_newer_user_turn_arrived():
    # A newer user message landed during the ~3s decision — must not overwrite it.
    h = _history()
    h.append_user("newer turn")
    assert h.replace_last_user("चल रही है", "corrected") is False
    assert h.messages[-1]["content"] == "newer turn"


def test_no_user_message_returns_false():
    h = ConversationHistory()
    h.setup_system_prompt({"role": ChatRole.SYSTEM, "content": "prompt"})
    assert h.replace_last_user("anything", "x") is False


def _sarvam_history():
    h = ConversationHistory()
    h.setup_system_prompt({"role": ChatRole.SYSTEM, "content": "prompt"})
    h.append_user("हां जी", asr_turn_id=1)
    return h


def test_cumulative_resend_replaces_same_asr_turn():
    h = _sarvam_history()
    assert h.replace_last_user_if_prefix("हां जी मेरा नाम विक्रम", asr_turn_id=1) is True
    users = [m for m in h.messages if m.get("role") == ChatRole.USER]
    assert len(users) == 1
    assert users[0]["content"] == "हां जी मेरा नाम विक्रम"


def test_new_asr_turn_appends_instead():
    h = _sarvam_history()
    assert h.replace_last_user_if_prefix("हां जी मेरा नाम विक्रम", asr_turn_id=2) is False
    users = [m for m in h.messages if m.get("role") == ChatRole.USER]
    assert len(users) == 1  # unchanged; caller appends the new turn


def test_non_prefix_appends():
    h = _sarvam_history()
    assert h.replace_last_user_if_prefix("phone number batao", asr_turn_id=1) is False


def test_identical_text_is_not_a_replace():
    h = _sarvam_history()
    assert h.replace_last_user_if_prefix("हां जी", asr_turn_id=1) is False
