"""Small formatting/mapping helpers for the numbers module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.schemas import PhoneNumberResponse


def to_responses(records: Sequence[PhoneNumber]) -> list[PhoneNumberResponse]:
    """Map stored numbers onto their API shape.

    Args:
        records: Numbers in repository order.

    Returns:
        One response per number, in the same order.
    """
    return [PhoneNumberResponse.from_model(record) for record in records]


def assigned_only(records: Sequence[PhoneNumber]) -> list[PhoneNumber]:
    """Keep only the numbers that route somewhere.

    Args:
        records: Numbers in repository order.

    Returns:
        The subset with an ``assigned_agent_id``.
    """
    return [record for record in records if record.assigned_agent_id]
