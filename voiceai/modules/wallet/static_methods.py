"""Pure, I/O-free wallet computations (AGENTS.md rule 1g)."""

from __future__ import annotations

from voiceai.modules.wallet.models import StoredTemplate
from voiceai.modules.wallet.schemas import WalletContract

TemplateSummary = WalletContract.TemplateSummary
Template = WalletContract.Template


def summarize_template(stored: StoredTemplate) -> TemplateSummary:
    """Project a stored row into the list-safe summary shape.

    Args:
        stored: The persisted template row.

    Returns:
        The summary view without the agent payload (copies the language list so
        callers cannot mutate the stored row through the view).
    """
    return TemplateSummary(
        template_id=stored.template_id,
        name=stored.name,
        industry=stored.industry,
        description=stored.description,
        languages=list(stored.languages),
    )


def render_template(stored: StoredTemplate) -> Template:
    """Project a stored row into the full template view.

    Args:
        stored: The persisted template row.

    Returns:
        The full view including a copy of the free-form agent payload.
    """
    return Template(
        template_id=stored.template_id,
        name=stored.name,
        industry=stored.industry,
        description=stored.description,
        languages=list(stored.languages),
        agent_payload=dict(stored.agent_payload),
    )
