"""Shared agent-schema leaves: welcome text, localized text, validators (spec 0019).

These lived in ``models/agent.py`` and were imported back by ``brains`` and
``pipeline``, forming the ``agent → tools → brains → agent`` cycle the import
gate forbids. They depend on nothing but ``constants`` and the stdlib, so they
are a natural leaf: every direction of the old cycle now points here and the
graph is acyclic. ``agent.py`` re-exports them, so existing
``models.agent.X`` import paths keep resolving.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from voiceai.modules.agents.constants import MODEL_REASONING_EFFORT_MAP

__all__ = [
    "AGENT_WELCOME_MESSAGE",
    "LocalizedText",
    "validate_attribute",
    "validate_reasoning_effort_for_model",
]

AGENT_WELCOME_MESSAGE = "This call is being recorded for quality assurance and training. Please speak now."

# A message that is either a single string or a per-language {lang_code: text} map.
LocalizedText = str | dict[str, str]

_T = TypeVar("_T")


def validate_attribute(value: _T, allowed_values: Sequence[str], value_type: str = "provider") -> _T:
    """Reject a value outside its allowed set with the legacy error text.

    Args:
        value: The candidate value (typically a provider name).
        allowed_values: Every accepted value.
        value_type: Label used in the error message.

    Returns:
        The value unchanged when it is allowed.

    Raises:
        ValueError: When the value is not in ``allowed_values``.
    """
    if value not in allowed_values:
        raise ValueError(f"Invalid value for {value_type}:'{value}' provided. Supported values: {allowed_values}.")
    return value


def validate_reasoning_effort_for_model(model: str, reasoning_effort: str) -> None:
    """Reject a reasoning effort a GPT model does not support; non-GPT models pass through.

    Args:
        model: Model name, optionally provider-prefixed ("azure/gpt-5").
        reasoning_effort: The requested effort value.

    Raises:
        ValueError: When the model's supported-effort list exists and excludes the value.
    """
    if "gpt" not in model:
        return

    if "/" in model:
        model = model.split("/")[-1]

    supported = MODEL_REASONING_EFFORT_MAP.get(model, None)
    if supported is not None and reasoning_effort not in supported:
        raise ValueError(f"reasoning_effort '{reasoning_effort}' is not supported for model '{model}'.")
