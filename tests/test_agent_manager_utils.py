"""Tests for agent_manager utils/static extraction (task_manager slim-down).

Pure functions moved verbatim from task_manager.py; these tests pin their
contracts at the new canonical addresses.
"""

import pytest

from voiceai.agent_manager.static_methods import build_lid_decision_record, inject_end_call_tool
from voiceai.agent_manager.utils import (
    HANDOFF_CLIP_CACHE_MAX,
    NON_NODE_RESPONSE_CATEGORIES,
    asr_id_to_int,
    is_alphanumeric_readout,
    s2s_ack_independent,
    s2s_talko_encoding,
    trailing_utterance_text,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("call 1234", True),
        ("otp 5678", True),
        ("hello world", False),
        ("", False),
        ("no digits here at all really", False),
        ("a1", True),
    ],
)
def test_is_alphanumeric_readout(text: str, expected: bool) -> None:
    """Digit-bearing short texts are readouts; prose is not."""
    assert is_alphanumeric_readout(text) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("turn_3", 3), ("7", 7), ("abc", None), (5, 5), (None, None)],
)
def test_asr_id_to_int(value: object, expected: object) -> None:
    """ASR id coercion: digit extraction, passthrough, and None."""
    assert asr_id_to_int(value) == expected


def test_trailing_utterance_text_takes_tail() -> None:
    """Only the trailing same-language run survives a gap boundary."""
    segments = [
        {"ts": 1.0, "lang": "en", "text": "old news", "audio_s": 0.5},
        {"ts": 10.0, "lang": "en", "text": "new request", "audio_s": 0.5},
    ]
    assert trailing_utterance_text(segments) == "new request"
    assert trailing_utterance_text([]) == ""
    changed = [
        {"ts": 1.0, "lang": "en", "text": "english bit", "audio_s": 0.5},
        {"ts": 1.6, "lang": "hi", "text": "hindi bit", "audio_s": 0.5},
    ]
    assert trailing_utterance_text(changed) == "hindi bit"


def test_s2s_toggles_default_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """S2S toggles default on/mulaw-8k and honor explicit overrides."""
    monkeypatch.delenv("S2S_ACK_INDEPENDENT", raising=False)
    monkeypatch.delenv("S2S_TALKO_ENCODING", raising=False)
    assert s2s_ack_independent() is True
    assert s2s_talko_encoding() == "mulaw-8k"
    monkeypatch.setenv("S2S_ACK_INDEPENDENT", "0")
    assert s2s_ack_independent() is False


def test_handoff_cache_constants() -> None:
    """Cache bound and response categories are sane."""
    assert HANDOFF_CLIP_CACHE_MAX == 256
    assert "handoff" in NON_NODE_RESPONSE_CATEGORIES


def test_build_lid_decision_record_shape() -> None:
    """Decision records carry the llm_switch flow contract."""
    record = build_lid_decision_record(
        outcome="stay",
        fired_at=100.0,
        now=100.5,
        active_transcript="hello",
        active="en",
        detector_transcript="hello",
        detector_lang_tag="en",
        decision={"target_language": "en", "target_confidence": 0.9, "reasoning": " x "},
        buffered_max_segment_s=1.2344,
        speculation_started=False,
    )
    assert record["flow"] == "llm_switch"
    assert record["decide_latency_ms"] == 500.0
    assert record["path"] == "turn_boundary"
    assert record["reasoning"] == "x"
    assert record["buffered_max_segment_s"] == 1.234


def test_inject_end_call_tool_idempotent() -> None:
    """Injection adds end_call once and never duplicates."""
    first = inject_end_call_tool(None, scope=None, nodes=["a"])
    assert "tools" in first and first["tools"]
    second = inject_end_call_tool(first, scope=None, nodes=["a"])
    assert len(second["tools"]) == len(first["tools"])
