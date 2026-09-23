"""Guard helpers that raise the wallet module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.wallet.errors import TemplateNotFoundError
from voiceai.modules.wallet.models import StoredTemplate


def ensure_template_found(stored: StoredTemplate | None, template_id: str) -> StoredTemplate:
    """Return the stored template or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        template_id: Requested template id, echoed in the error details.

    Returns:
        The stored template unchanged.

    Raises:
        TemplateNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise TemplateNotFoundError(template_id)
    return stored
