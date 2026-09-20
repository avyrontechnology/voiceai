"""Error types raised by the voice module (AGENTS.md rule 1c).

All extend `VoiceError`, so callers may catch the whole module's failures with one clause
while every error still serialises through the common `AppError` envelope. The component
subclasses mirror the legacy attribution contract (``voiceai.exceptions``'s
``component`` / ``provider`` / ``model`` fields) that ``run()``'s error attribution at
task_manager.py:8643-8701 reads.

TODO(spec-0004): the adapters (step B3) alias `TranscriptionError`/`SynthesisError` to
the legacy ``TranscriberError``/``SynthesizerError`` classes so that attribution keeps
working across the seam; the layer contract bans that legacy import here (§3.1 — only
``adapters/`` may hold it).
"""

from __future__ import annotations

from typing import Any, ClassVar

from voiceai.common.constants import HTTP_BAD_REQUEST, HTTP_CONFLICT
from voiceai.common.errors import AppError, ErrorCode
from voiceai.modules.voice.constants import (
    COMPONENT_LLM,
    COMPONENT_S2S,
    COMPONENT_SYNTHESIZER,
    COMPONENT_TRANSCRIBER,
)

__all__ = [
    "LlmError",
    "PlaceCallError",
    "TalkoPartnerExistsError",
    "S2SError",
    "SynthesisError",
    "TranscriptionError",
    "UnknownComponentLabelError",
    "UnknownTalkoPartnerError",
    "VoiceComponentError",
    "VoiceError",
]


class VoiceError(AppError):
    """Base of the voice-module hierarchy: an unexpected failure inside the call runtime.

    Inherits `INTERNAL_ERROR`/500 semantics, so an uncategorised failure reaches any
    client only as the opaque envelope while the stack goes to the log (AGENTS.md §4).
    """


class UnknownComponentLabelError(VoiceError):
    """A pool/handler label was addressed that no configured component carries.

    Mirrors the pools' legacy ``ValueError("Unknown ... label ...")`` guards as a module
    error, so new composition code (step B4 onward) never leaks a builtin across a layer
    boundary; the legacy call sites keep their verbatim raises until their own steps.
    """


class VoiceComponentError(VoiceError):
    """A realtime component failed, with the attribution fields observability reads.

    Carries the same ``component`` / ``provider`` / ``model`` triple as the legacy
    ``VoiceAIComponentError``, because the request log's error rows are built from
    exactly those attributes (task_manager.py:8650-8674, preserved contract).

    Args:
        message: Operator-facing description (never echoed verbatim to clients — the
            base class keeps internal errors opaque).
        component: Which component failed; one of the `constants.COMPONENT_*` values.
        provider: The failing provider's name, when known.
        model: The failing model's name, when known.
        details: Structured client-safe context, passed through to `AppError`.
        cause: Originating exception, chained for the log stack.
    """

    #: Subclasses pin their component name; the base leaves it to the constructor.
    component_name: ClassVar[str | None] = None

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        details: dict[str, Any] | None = None,  # why: details are arbitrary JSON-able context
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, details=details, cause=cause)
        self.component: str | None = component or self.component_name
        self.provider: str | None = provider
        self.model: str | None = model


class TranscriptionError(VoiceComponentError):
    """The ASR side failed (connection, stream, or provider fault)."""

    component_name: ClassVar[str | None] = COMPONENT_TRANSCRIBER


class SynthesisError(VoiceComponentError):
    """The TTS side failed (connection, stream, or provider fault)."""

    component_name: ClassVar[str | None] = COMPONENT_SYNTHESIZER


class LlmError(VoiceComponentError):
    """The generation LLM failed mid-call."""

    component_name: ClassVar[str | None] = COMPONENT_LLM


class S2SError(VoiceComponentError):
    """The speech-to-speech provider session failed."""

    component_name: ClassVar[str | None] = COMPONENT_S2S


class PlaceCallError(VoiceError):
    """An outbound place-call request failed validation or dialing (spec 0008).

    Raised for undialable destinations and trunk refusals alike; the envelope
    carries the operator-safe message while identifiers stay in details.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class UnknownTalkoPartnerError(PlaceCallError):
    """A `partner_id` named no stored partner record (spec 0008).

    Fail-closed by design: the service never dials on another partner's
    credentials, so an unknown id is a 400, never a fallback.
    """


class TalkoPartnerExistsError(PlaceCallError):
    """A partner record already exists for the requested id (spec 0008)."""

    code: ClassVar[ErrorCode] = ErrorCode.CONFLICT
    http_status: ClassVar[int] = HTTP_CONFLICT
