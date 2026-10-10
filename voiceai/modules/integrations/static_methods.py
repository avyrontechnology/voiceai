"""Pure, I/O-free credential handling (spec 0077).

This file is the module's security contract. `mask_secrets` decides what leaves
the process; `merge_config` decides what a client can write without destroying
what it was never shown.
"""

from __future__ import annotations

from typing import Any

from voiceai.modules.integrations.constants import MASKED_SECRET, SECRET_HINTS

__all__ = ["is_secret_field", "mask_secrets", "merge_config"]


def is_secret_field(field: str) -> bool:
    """Report whether a config field name looks like it holds a credential.

    Args:
        field: The config key.

    Returns:
        True when the name carries one of the secret hints, case-insensitively.
    """
    lowered = field.lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def mask_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """Replace secret-looking values for API responses. Raw values stay in the store.

    Only **string** values are masked, exactly as the legacy helper did: a nested
    dict or a numeric token under a secret-looking name is passed through, so
    masking cannot mangle a structure it does not understand.

    Args:
        config: The stored config.

    Returns:
        A copy safe to serialise into a response.
    """
    masked: dict[str, Any] = {}
    for field, value in config.items():
        if isinstance(value, str) and is_secret_field(field):
            masked[field] = MASKED_SECRET
        else:
            masked[field] = value
    return masked


def merge_config(stored: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge a client-supplied config over the stored one, per key.

    A key whose incoming value is exactly the mask marker is **skipped**. That is
    the fix for a destructive round-trip: a client reads an integration, gets
    `••••••••` back for its API key, and PUTs the response it just read — which
    under a plain `{**stored, **incoming}` would overwrite the real credential
    with eight bullet characters, unrecoverably. Skipping the marker means
    "leave this field alone", so the echo-back case is a no-op and a genuinely
    new key still merges (spec 0077).

    Args:
        stored: The config currently in the store.
        incoming: The config from the request.

    Returns:
        The merged config to persist.
    """
    merged = dict(stored)
    for field, value in incoming.items():
        if value == MASKED_SECRET:
            continue
        merged[field] = value
    return merged
