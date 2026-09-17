"""Provider-agnostic speech-to-speech events and usage records (spec 0004, step B5).

Moved verbatim from ``voiceai/s2s/events.py`` (now a spec-0004-tagged legacy shim
re-exporting this module, so every legacy import resolves to these same class
objects — the session runner's ``isinstance`` dispatch depends on that identity).
Mechanical accommodations only: this docstring, ``__all__``, class docstrings the
strict profile requires, and PEP 604 unions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum

__all__ = [
    "AudioDelta",
    "AudioEncoding",
    "AudioFormat",
    "FunctionCall",
    "FunctionCallCancelled",
    "InputTranscript",
    "Interrupted",
    "ResponseDone",
    "S2SError",
    "S2SUsage",
    "SessionExpiring",
    "SessionReady",
    "SessionResumed",
    "TranscriptDelta",
]


class AudioEncoding(str, Enum):
    """Wire encoding of one audio leg (mu-law for carriers, linear PCM elsewhere)."""

    MULAW = "mulaw"
    PCM = "pcm"


@dataclass(frozen=True)
class AudioFormat:
    """How audio is carried on one leg of the media stream."""

    encoding: AudioEncoding
    sample_rate: int


@dataclass(frozen=True)
class S2SUsage:
    """Tokens for one turn. Audio and text bill at different rates, so they stay apart."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    input_audio_tokens: int = 0
    input_text_tokens: int = 0
    output_audio_tokens: int = 0
    output_text_tokens: int = 0

    def __add__(self, other: S2SUsage) -> S2SUsage:
        """Field-wise sum, used to accumulate a session total across turns."""
        return replace(self, **{f: getattr(self, f) + getattr(other, f) for f in _USAGE_FIELDS})

    def modality_split(self) -> dict:
        """The audio/text breakdown that has to reach billing."""
        return {
            "input_audio_tokens": self.input_audio_tokens,
            "input_text_tokens": self.input_text_tokens,
            "output_audio_tokens": self.output_audio_tokens,
            "output_text_tokens": self.output_text_tokens,
        }

    def as_dict(self) -> dict:
        """All counters as a plain dict (the request-log payload shape)."""
        return asdict(self)


_USAGE_FIELDS = tuple(S2SUsage.__dataclass_fields__)


@dataclass
class SessionReady:
    """Provider accepted the session config and is ready for audio."""

    connection_time_ms: float


@dataclass
class AudioDelta:
    """A chunk of output audio, PCM-16 at the provider's output_sample_rate."""

    data: bytes


@dataclass
class TranscriptDelta:
    """Assistant speech transcript."""

    content: str
    is_final: bool


@dataclass
class InputTranscript:
    """User speech transcript from the provider's built-in transcription."""

    content: str
    is_final: bool


@dataclass
class FunctionCall:
    """The provider wants to invoke a tool."""

    name: str
    call_id: str
    arguments: str  # JSON string


@dataclass
class FunctionCallCancelled:
    """Provider withdrew tool calls it requested."""

    call_ids: list


@dataclass
class ResponseDone:
    """A full model response (turn) has completed."""

    transcript: str
    usage: S2SUsage | None = field(default=None)


@dataclass
class Interrupted:
    """User barged in, provider stopped generating."""


@dataclass
class SessionExpiring:
    """The provider will close this session soon and it must be resumed."""

    time_left_ms: int | None = None


@dataclass
class SessionResumed:
    """A dropped session was transparently re-established mid-call."""

    reconnect_ms: float


@dataclass
class S2SError:
    """An error from the S2S provider."""

    message: str
    code: str = ""
    fatal: bool = True
