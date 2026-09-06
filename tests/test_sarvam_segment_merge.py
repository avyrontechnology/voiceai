"""Sarvam segment overlap-merge: segments re-emit the previous tail, so naive
joining doubles it ("8 7 4 2" + "8 7 4 2" -> "8 7 4 2 8 7 4 2"), corrupting turn
metrics, LLM history and the transcript panel.
"""

from voiceai.transcriber.sarvam_transcriber import merge_transcript_segments as merge


def test_exact_duplicate_segment_adds_nothing():
    assert merge("8 7 4 2", "8 7 4 2") == "8 7 4 2"


def test_partial_overlap_appends_only_new_words():
    assert merge("हां जी मेरा नाम", "मेरा नाम विक्रम") == "हां जी मेरा नाम विक्रम"


def test_cumulative_reemission_extends():
    assert merge("हां जी", "हां जी मेरा नाम विक्ष") == "हां जी मेरा नाम विक्ष"


def test_disjoint_segments_join_with_space():
    assert merge("hello", "world") == "hello world"


def test_empty_sides():
    assert merge("", "hello") == "hello"
    assert merge("hello", "") == "hello"
    assert merge("", "") == ""


def test_no_mid_word_merge():
    # "chikara" vs "chikaran" share chars but not words — must not merge.
    assert merge("vikram chikara", "chikaran") == "vikram chikara chikaran"
