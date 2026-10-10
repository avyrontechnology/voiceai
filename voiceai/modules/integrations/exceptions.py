"""Guard helpers that raise the integrations module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.integrations.errors import IntegrationNotFoundError
from voiceai.modules.integrations.models import Integration


def ensure_integration_found(stored: Integration | None, integration_id: str) -> Integration:
    """Return the stored integration or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        integration_id: Requested integration id, echoed in the error details.

    Returns:
        The stored integration unchanged.

    Raises:
        IntegrationNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise IntegrationNotFoundError(integration_id)
    return stored
