"""Opaque id minting shared across modules (spec 0005, C2)."""

from voiceai.common.ids import new_id


def test_ids_carry_the_prefix_and_unique_suffixes() -> None:
    """Prefixed, unique, log-readable identifiers."""
    first, second = new_id("usr"), new_id("usr")

    assert first.startswith("usr_") and second.startswith("usr_")
    assert first != second
    assert len(first) == len("usr_") + 12
    int(first.split("_")[1], 16)  # non-hex suffix would raise
