"""Phone number units: the Talko normalization contract and the resolve payload.

The normalization vectors are the important ones. `normalize_phone_digits` must
agree digit-for-digit with Talko's own normalizer or inbound call routing breaks
silently, so the cases below are the contract, not examples.
"""

from __future__ import annotations

from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.static_methods import normalize_phone_digits, resolve_payload, same_number

# (input, expected) — the variants a carrier can hand us for one Indian DID.
NORMALIZATION_VECTORS: tuple[tuple[str, str], ...] = (
    ("+9179876543210", "9179876543210"),
    ("9179876543210", "9179876543210"),
    ("91 79-876543210", "9179876543210"),
    ("0917876543210", "0917876543210"),  # 13 digits: falls through to digits-only
    ("09876543210", "919876543210"),  # leading trunk zero is dropped
    ("7876543210", "917876543210"),  # bare 10-digit subscriber number
    ("  +91 79 876 543 210  ", "9179876543210"),
    ("", ""),
    ("abc", ""),
    ("+", ""),
    ("+1 202 555 0143", "12025550143"),  # non-Indian: digits only
)


def test_normalization_vectors() -> None:
    """Every dialed format maps to the same lookup key."""
    for raw, expected in NORMALIZATION_VECTORS:
        assert normalize_phone_digits(raw) == expected, raw


def test_formats_of_one_number_are_equal() -> None:
    """The comparison helper agrees across every format of one DID."""
    assert same_number("+9179876543210", "91 79-876543210")
    assert same_number("  +91 79 876 543 210 ", "9179876543210")
    assert not same_number("+9179876543210", "+9179876543211")
    assert not same_number("", "")


def test_a_ten_digit_and_an_eleven_digit_form_are_different_numbers() -> None:
    """`09876543210` and `7876543210` are NOT the same DID.

    Both are accepted shapes on their own, but they normalize differently
    (`91…876543210` vs `91…76543210`), so they are distinct numbers. The legacy
    normalizer behaves identically — this pins that surprising pairing rather
    than assuming the trunk-zero form collapses onto the bare 10-digit one.
    """
    assert not same_number("09876543210", "7876543210")


def test_normalization_matches_the_retired_legacy_helper() -> None:
    """The moved helper still agrees with the legacy implementation it replaced.

    The legacy store re-exports this function now, so this pins that the
    re-export — not a second copy — is what legacy callers get.
    """
    from voiceai.platform.store import normalize_phone_digits as legacy

    for raw, _ in NORMALIZATION_VECTORS:
        assert normalize_phone_digits(raw) == legacy(raw)


def test_resolve_payload_carries_only_the_routing_target() -> None:
    """The Talko resolve body never carries the DID or the stored document."""
    record = PhoneNumber(id="n1", number="+9179876543210", assigned_agent_id="agent-1")

    payload = resolve_payload(record)

    assert payload == {"agent_id": "agent-1"}
    assert "+9179876543210" not in str(payload)


def test_resolve_payload_includes_the_talko_owner_when_set() -> None:
    """A Talko owner makes it into the body so Talko can dial without its own row."""
    record = PhoneNumber(
        id="n1",
        number="+9179876543210",
        assigned_agent_id="agent-1",
        talko_partner_id=42,
        talko_vendor_config_id="vc-1",
    )

    assert resolve_payload(record) == {
        "agent_id": "agent-1",
        "talko_partner_id": 42,
        "talko_vendor_config_id": "vc-1",
    }


def test_resolve_payload_omits_absent_talko_keys() -> None:
    """Unset owner fields are absent, not null — Talko reads them as absent."""
    record = PhoneNumber(id="n1", number="+9179876543210", assigned_agent_id="agent-1", talko_partner_id=None)

    assert "talko_partner_id" not in resolve_payload(record)
    assert "talko_vendor_config_id" not in resolve_payload(record)
