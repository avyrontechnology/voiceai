"""Pure, I/O-free phone number helpers (spec 0074).

`normalize_phone_digits` is the contract with Talko: both sides must map
`+9179…`, `9179…`, `91 79-…` and 10-digit variants to the same key, or inbound
call routing breaks. It is carried over verbatim from `platform/store.py` — a
"cleanup" here is a production outage, so the migration copies it and the test
vectors pin it.
"""

from __future__ import annotations

import re
from typing import Any

from voiceai.modules.numbers.constants import (
    FULL_INDIAN_DIGITS_LENGTH,
    INDIAN_COUNTRY_CODE,
    SUBSCRIBER_DIGITS_LENGTH,
    TRUNK_PREFIXED_LENGTH,
)
from voiceai.modules.numbers.models import PhoneNumber

__all__ = ["normalize_phone_digits", "resolve_payload", "same_number"]

_NON_DIGITS = re.compile(r"\D")


def normalize_phone_digits(raw: str) -> str:
    """Normalize a phone number to engine-lookup digits (no '+', spaces, dashes).

    Mirrors Talko's ``normalize_phone_number(..., with_plus=False)`` so both
    sides agree: ``+9179…``, ``9179…``, ``91 79-…`` and 10-digit variants all
    map to the same ``91XXXXXXXXXX`` key. Non-Indian/short inputs fall back to
    digits-only.

    Args:
        raw: The number as stored or as dialled.

    Returns:
        The normalized digits, or ``""`` when the input carries none.
    """
    if not raw:
        return ""
    digits = _NON_DIGITS.sub("", raw.strip())
    if not digits:
        return ""
    if digits.startswith(INDIAN_COUNTRY_CODE) and len(digits) == FULL_INDIAN_DIGITS_LENGTH:
        return digits
    if len(digits) == TRUNK_PREFIXED_LENGTH and digits.startswith("0"):
        return INDIAN_COUNTRY_CODE + digits[1:]
    if len(digits) == SUBSCRIBER_DIGITS_LENGTH:
        return INDIAN_COUNTRY_CODE + digits
    return digits


def same_number(left: str, right: str) -> bool:
    """Report whether two DIDs normalize to the same lookup key.

    Args:
        left: First number, in any format.
        right: Second number, in any format.

    Returns:
        True when both normalize to the same non-empty key.
    """
    key = normalize_phone_digits(left)
    return bool(key) and key == normalize_phone_digits(right)


def resolve_payload(record: PhoneNumber) -> dict[str, Any]:
    """Build the Talko resolve response body for an assigned number.

    Only the routing target and the Talko owner keys are returned — never the
    DID itself and never the stored document (spec 0074, security notes).

    Args:
        record: The assigned number.

    Returns:
        The response body Talko's inbound lookup expects.
    """
    payload: dict[str, Any] = {"agent_id": record.assigned_agent_id}
    if record.talko_partner_id is not None:
        payload["talko_partner_id"] = record.talko_partner_id
    if record.talko_vendor_config_id:
        payload["talko_vendor_config_id"] = record.talko_vendor_config_id
    return payload
