"""Pure catalog helpers: language validation and natural-key building (spec 0022).

No I/O, no imports beyond stdlib — deterministic and unit-tested in isolation.
"""

from __future__ import annotations

import re

from voiceai.modules.catalog.constants import CATALOG_ID_SEPARATOR

__all__ = ["build_catalog_id", "is_valid_language"]

#: BCP-47 well-formed language tag (spec 0022): 2-3 letter primary, optional
#: script (`Latn`) and region (`IN`) or numeric area (`419`). Validation accepts
#: any well-formed code — the catalog's `languages` are dropdown suggestions,
#: not a closed set (codes are a standard, not provider folklore).
_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$")


def is_valid_language(code: str) -> bool:
    """Return whether `code` is a well-formed BCP-47 language tag.

    Args:
        code: Candidate tag (`en`, `hi`, `en-US`, `zh-Hant-TW`).

    Returns:
        `True` for well-formed tags; anything else (empty, `_` separators,
        overlong subtags) is `False`.
    """
    return bool(_LANGUAGE_PATTERN.fullmatch(code))


def build_catalog_id(modality: str, provider: str, model: str) -> str:
    """Build the natural key `{modality}:{provider}:{model}`.

    Args:
        modality: One of the four modalities.
        provider: Registry provider key, verbatim.
        model: Provider model identifier, verbatim.

    Returns:
        The catalog natural key (also pinned as the document `id`).
    """
    return CATALOG_ID_SEPARATOR.join((modality, provider, model))
