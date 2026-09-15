"""Pure utilities for the agent_manager package.

Stateless helpers extracted from task_manager: audio resampling, S2S
toggles, readout detection, ASR id coercion, utterance slicing, and the
process-wide handoff-clip cache. No TaskManager state, no I/O.
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from typing import Any, Dict, FrozenSet, List, Optional, cast

from voiceai.agent_manager.constants import (
    DEFAULT_S2S_ACK_INDEPENDENT,
    DEFAULT_S2S_TALKO_ENCODING,
    S2S_ACK_INDEPENDENT_ENV,
    S2S_TALKO_ENCODING_ENV,
)
from voiceai.core.environment import get_str
from voiceai.helpers.utils import resample

__all__ = [
    "HANDOFF_CLIP_CACHE",
    "HANDOFF_CLIP_CACHE_MAX",
    "asr_id_to_int",
    "is_alphanumeric_readout",
    "trailing_utterance_text",
    "welcome_pcm_upsampled",
]


@lru_cache(maxsize=256)
def welcome_pcm_upsampled(welcome_b64: str, target_sample_rate: int, source_sample_rate: int = 8000) -> bytes:
    """Upsample the cached welcome PCM to the raw-PCM output rate (web/freeswitch), memoized
    per (welcome, rates). The welcome is identical across every call of an agent, so resampling it
    once — instead of in each TaskManager.__init__ — keeps CPU from spiking when many calls start
    at once (the welcome burst). Welcomes already at the target rate pass through untouched.

    Args:
        welcome_b64: Base64 welcome audio.
        target_sample_rate: Output sample rate.
        source_sample_rate: Source sample rate.

    Returns:
        The (possibly resampled) PCM bytes.
    """
    decoded = base64.b64decode(welcome_b64)
    if source_sample_rate == target_sample_rate:
        return decoded
    # resample is legacy-untyped; the cast pins the boundary contract.
    return cast(
        bytes,
        resample(  # type: ignore[no-untyped-call]
            decoded,
            target_sample_rate=target_sample_rate,
            format="pcm",
            original_sample_rate=source_sample_rate,
        ),
    )


def s2s_ack_independent() -> bool:
    """Whether S2S output progresses ack-independently (default safe = on).

    ``S2S_ACK_INDEPENDENT=0`` restores the pure legacy path (no playout-estimate
    fallback, no ack-silence logging).

    Returns:
        True unless explicitly disabled.
    """
    return (get_str(S2S_ACK_INDEPENDENT_ENV, DEFAULT_S2S_ACK_INDEPENDENT) or DEFAULT_S2S_ACK_INDEPENDENT).strip() == "1"


def s2s_talko_encoding() -> str:
    """Expected Talko leg encoding (default safe = ``mulaw-8k``).

    Returns:
        The normalized encoding name.
    """
    return (get_str(S2S_TALKO_ENCODING_ENV, DEFAULT_S2S_TALKO_ENCODING) or DEFAULT_S2S_TALKO_ENCODING).strip().lower()


def is_alphanumeric_readout(text: str) -> bool:
    """Whether text is a code readout, not language evidence (rule 3a).

    Args:
        text: Transcript text.

    Returns:
        True for >=1 digit-bearing token with <=2 other tokens.
    """
    tokens = re.findall(r"\w+", text or "", flags=re.UNICODE)
    if not tokens:
        return False
    code_tokens = [t for t in tokens if any(ch.isdigit() for ch in t)]
    return len(code_tokens) >= 1 and (len(tokens) - len(code_tokens)) <= 2


def asr_id_to_int(value: Any) -> Optional[int]:
    """Coerce ASR turn ids to int.

    Args:
        value: OpenAI "turn_3" strings (digit-extracted) or Deepgram ints.

    Returns:
        The int id, or None when unparseable (passthrough for non-strings).
    """
    if isinstance(value, str):
        digits = "".join(filter(str.isdigit, value))
        return int(digits) if digits else None
    return cast(Optional[int], value)


def trailing_utterance_text(segments: Any, gap_seconds: float = 4.0) -> str:
    """Text of the caller's LAST utterance: trailing detector segments in the same
    language, back to a real silence boundary. Breaks on either a gap > gap_seconds
    (4s ≈ an utterance boundary, so a request that pauses to think is kept together,
    unlike a mid-sentence 1.5s) OR a language change (a distinct/stale prior utterance).
    Subtractive; returns '' so callers fall back to the full transcript.

    Args:
        segments: Detector segments (dicts with ts/lang/text/audio_s).
        gap_seconds: Silence boundary width.

    Returns:
        The trailing utterance text (possibly empty).
    """
    tail: List[str] = []
    prev_start: Optional[float] = None
    tail_lang: Optional[str] = None
    for seg in reversed(segments or []):
        ts = seg.get("ts")
        lang = seg.get("lang")
        # Segments arrive at speech END, so a segment's own start = ts - audio_s.
        if prev_start is not None:
            gap_too_big = ts is None or prev_start - ts > gap_seconds
            lang_changed = bool(tail_lang) and bool(lang) and lang != tail_lang
            if gap_too_big or lang_changed:
                break
        if seg.get("text"):
            tail.append(seg["text"])
            tail_lang = tail_lang or lang
        prev_start = (ts - (seg.get("audio_s") or 0.0)) if ts is not None else None
    return " ".join(reversed(tail)).strip()


# Handoff clips are static per (voice, text): cache across calls so an N-language agent
# doesn't pay N TTS renders per call, concurrent with the welcome message. Process-wide.
HANDOFF_CLIP_CACHE: Dict[str, Any] = {}
HANDOFF_CLIP_CACHE_MAX = 256

NON_NODE_RESPONSE_CATEGORIES: FrozenSet[str] = frozenset(
    {"is_user_online_message", "filler", "backchanneling", "agent_welcome_message", "handoff"}
)
