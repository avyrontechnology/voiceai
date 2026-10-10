"""Small formatting/mapping helpers for the webhooks module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.webhooks.models import Webhook
from voiceai.modules.webhooks.schemas import WebhookResponse


def to_responses(records: Sequence[Webhook]) -> list[WebhookResponse]:
    """Map stored webhooks onto their API shape.

    Args:
        records: Webhooks in repository order.

    Returns:
        One response per record, in the same order.
    """
    return [WebhookResponse.from_model(record) for record in records]


def owned_by(records: Sequence[Webhook], agent_id: str) -> list[Webhook]:
    """Return the hooks explicitly owned by an agent (tenant-wide ones excluded).

    Args:
        records: Webhooks in repository order.
        agent_id: The agent to filter for.

    Returns:
        The agent's own hooks, in the input order.
    """
    return [record for record in records if record.agent_id == agent_id]
