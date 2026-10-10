"""Small formatting/mapping helpers for the integrations module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.integrations.models import Integration
from voiceai.modules.integrations.schemas import IntegrationResponse


def to_responses(records: Sequence[Integration]) -> list[IntegrationResponse]:
    """Map stored integrations onto their API shape.

    Callers pass already-masked records (the service is what masks), so this
    helper cannot accidentally serialize a raw credential.

    Args:
        records: Integrations in repository order.

    Returns:
        One response per record, in the same order.
    """
    return [IntegrationResponse.from_model(record) for record in records]


def of_kind(records: Sequence[Integration], kind: str) -> list[Integration]:
    """Return the integrations for one provider.

    Args:
        records: Integrations in repository order.
        kind: The provider to filter for.

    Returns:
        The matching integrations, in the input order.
    """
    return [record for record in records if record.kind == kind]
