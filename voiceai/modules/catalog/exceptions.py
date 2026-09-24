"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.catalog.errors import CatalogNotFoundError

__all__ = ["ensure_catalog_entry"]


def ensure_catalog_entry(value: Any | None, kind: str, name: str, valid: list[str]) -> Any:
    """Return the catalog row, or raise a 404 naming the valid values.

    Args:
        value: The looked-up row, or ``None``.
        kind: What was addressed (`modality`, `provider`, `model`).
        name: The offending value (echoed — provider names are not secrets).
        valid: The values that would have resolved (dropdown truth).

    Returns:
        The row, narrowed to non-``None``.

    Raises:
        CatalogNotFoundError: When the value is ``None``.
    """
    if value is None:
        raise CatalogNotFoundError(
            f"Unknown catalog {kind} {name!r}.",
            details={kind: name, "valid": valid},
        )
    return value
