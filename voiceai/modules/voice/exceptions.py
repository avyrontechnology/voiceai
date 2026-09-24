"""Guard helpers that raise the voice module's errors (AGENTS.md rule 1c).

Composition and session code reads as assertions — ``label = ensure_label_known(label,
pool.labels)`` — without scattering ``raise`` statements or leaking builtins across a
layer boundary. The legacy pools keep their verbatim ``ValueError`` guards until their
own migration steps; these helpers are for NEW code only.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from voiceai.modules.voice import static_methods
from voiceai.modules.voice.constants import (
    AGENT_ID_KEY,
    AVAILABLE_LABELS_KEY,
    LABEL_KEY,
    PARTNER_ID_KEY,
    TO_NUMBER_KEY,
    UNKNOWN_LABEL_MESSAGE_TEMPLATE,
)
from voiceai.modules.voice.errors import (
    PlaceCallError,
    UnknownAgentError,
    UnknownComponentLabelError,
    UnknownTalkoPartnerError,
)
from voiceai.modules.voice.models import TalkoPartnerConfig

__all__ = ["ensure_label_known"]


def ensure_label_known(label: str, available: Collection[str]) -> str:
    """Return ``label``, or raise when no configured component carries it.

    Mirrors the legacy pool guards (``TranscriberPool.__init__``/``switch`` and their
    synthesizer twins) as a module error for the composition root (step B4 onward).

    Args:
        label: The pool/handler label being addressed.
        available: The labels the pool was actually built with.

    Returns:
        The label, guaranteed to be one of ``available``.

    Raises:
        UnknownComponentLabelError: When ``label`` is not in ``available``; the details
            carry the label and the available set (identifiers only, never payloads).
    """
    if label not in available:
        raise UnknownComponentLabelError(
            UNKNOWN_LABEL_MESSAGE_TEMPLATE.format(label=label),
            details={LABEL_KEY: label, AVAILABLE_LABELS_KEY: sorted(available)},
        )
    return label


__all__ += ["ensure_agent_known", "ensure_recipient_dialable", "ensure_talko_partner_known"]


def ensure_agent_known(agent_id: str, config: dict[str, Any] | None) -> dict[str, Any]:
    """Return the agent definition, or raise fail-closed on unknown ids.

    The definitions port is tenant-scoped: unknown and foreign ids both read
    as missing, so a cross-tenant dial fails here with no oracle, never
    reaching the trunk (spec 0021, M2).

    Args:
        agent_id: The requested agent definition id.
        config: The scoped port's definition payload, or `None`.

    Returns:
        The definition payload, guaranteed present.

    Raises:
        UnknownAgentError: When no visible definition exists for `agent_id`.
    """
    if config is None:
        raise UnknownAgentError(
            f"Unknown agent {agent_id!r}.",
            details={AGENT_ID_KEY: agent_id},
        )
    return config


def ensure_talko_partner_known(partner_id: str, record: TalkoPartnerConfig | None) -> TalkoPartnerConfig:
    """Return the partner record, or raise fail-closed on unknown ids.

    The service never dials on another partner's credentials, so a missing
    record is an error, never a fallback to trunk defaults.

    Args:
        partner_id: The requested Talko partner account id.
        record: The repository's record for it, or `None`.

    Returns:
        The record, guaranteed present.

    Raises:
        UnknownTalkoPartnerError: When no record exists for `partner_id`.
    """
    if record is None:
        raise UnknownTalkoPartnerError(
            f"Unknown Talko partner {partner_id!r}.",
            details={PARTNER_ID_KEY: partner_id},
        )
    return record


def ensure_recipient_dialable(to_number: str) -> str:
    """Return dialable digits, converting the static validator's `ValueError`.

    Args:
        to_number: The destination in any common format.

    Returns:
        The digits-only destination.

    Raises:
        PlaceCallError: When the destination is not dialable.
    """
    try:
        return static_methods.validate_recipient_number(to_number)
    except ValueError as exc:
        raise PlaceCallError(str(exc), details={TO_NUMBER_KEY: to_number}) from exc
