"""Pure functions of the call runtime, moved verbatim from task_manager.py 124-272 (B3).

Every function here is deterministic and I/O-free (AGENTS.md rule 1g): welcome-PCM
preparation (memoized), the end-call tool injection, the rule-3a code-readout gate, the
LID telemetry record builder, ASR turn-id coercion, and the trailing-utterance cut.
``voiceai/agent_manager/task_manager.py`` keeps SAME-NAMED module-level delegator
bindings for all six, so every legacy import and monkeypatch string path
(``voiceai.agent_manager.task_manager.<name>``) keeps resolving unchanged.

Bodies are verbatim; only the signatures gained annotations (mechanical rule-6
accommodation, reported in the B3 step notes). The two legacy values the bodies read —
``resample`` and the end-call tool constants — arrive through the ``adapters`` package
surface (§3.1 bridge 1), never from a legacy import here; an arch canary pins that this
module drags no provider stack in. The ``HANDOFF_CLIP_CACHE`` trio of the same
task_manager region deliberately did NOT move: it is process-wide MUTABLE state, not a
pure function, and it relocates with its owning subsystem (steps B8/B9).
"""

from __future__ import annotations

import base64
import copy
import json
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from voiceai.enums import ToolScope
from voiceai.modules.voice.adapters import END_CALL_FUNCTION_PREFIX, END_CALL_TOOL_DEFINITION, resample

__all__ = [
    "_inject_end_call_tool",
    "asr_id_to_int",
    "build_lid_decision_record",
    "is_alphanumeric_readout",
    "trailing_utterance_text",
    "welcome_pcm_upsampled",
]


@lru_cache(maxsize=256)
def welcome_pcm_upsampled(welcome_b64: str, target_sample_rate: int, source_sample_rate: int = 8000) -> bytes:
    """Upsample the cached welcome PCM to the raw-PCM output rate (web/freeswitch), memoized
    per (welcome, rates). The welcome is identical across every call of an agent, so resampling it
    once — instead of in each TaskManager.__init__ — keeps CPU from spiking when many calls start
    at once (the welcome burst). Welcomes already at the target rate pass through untouched."""
    decoded = base64.b64decode(welcome_b64)
    if source_sample_rate == target_sample_rate:
        return decoded
    return resample(
        decoded,
        target_sample_rate=target_sample_rate,
        format="pcm",
        original_sample_rate=source_sample_rate,
    )


def _inject_end_call_tool(
    api_tools: dict[str, Any] | None,  # why: the stored tools config is a free-form legacy dict
    *,
    scope: ToolScope | None,
    nodes: Iterable[str] | None,
    description: str | None = None,
) -> dict[str, Any]:  # why: answers the same free-form tools dict, mutated in place
    """Add the internal end_call tool (with scope/nodes) to api_tools; no-op if already present."""
    if api_tools is None:
        api_tools = {"tools": [], "tools_params": {}}
    if END_CALL_FUNCTION_PREFIX in api_tools.get("tools_params", {}):
        return api_tools  # already injected by the experiment path or a prior call
    tool_def = copy.deepcopy(END_CALL_TOOL_DEFINITION)
    if description:
        tool_def["function"]["description"] = description
    tools_list = api_tools.get("tools", [])
    if isinstance(tools_list, str):
        tools_list = json.loads(tools_list)
    tools_list.append(tool_def)
    api_tools["tools"] = tools_list
    api_tools.setdefault("tools_params", {})[END_CALL_FUNCTION_PREFIX] = {
        "pre_call_message": None,
        "scope": scope.value if scope else None,
        "nodes": list(nodes or []),
    }
    return api_tools


def is_alphanumeric_readout(text: str) -> bool:
    """Code readout, not language evidence (rule 3a): ≥1 digit-bearing token, ≤2 others."""
    tokens = re.findall(r"\w+", text or "", flags=re.UNICODE)
    if not tokens:
        return False
    code_tokens = [t for t in tokens if any(ch.isdigit() for ch in t)]
    return len(code_tokens) >= 1 and (len(tokens) - len(code_tokens)) <= 2


def build_lid_decision_record(
    *,
    outcome: str,
    fired_at: float,
    now: float,
    active_transcript: str | None,
    active: str | None,
    detector_transcript: str | None,
    detector_lang_tag: str | None,
    decision: dict[str, Any] | None,  # why: the Switch-LLM's judged payload is a free-form dict
    buffered_max_segment_s: float,
    speculation_started: bool,
    switched_to: str | None = None,
    context_note: str | None = None,
    detector_lang_confidence: float | None = None,
    detector_segments: list[dict[str, Any]] | None = None,  # why: free-form detector segment dicts
    inflight_activity: dict[str, Any] | None = None,  # why: free-form response-state snapshot
) -> dict[str, Any]:  # why: persisted JSONB row; `models.LidDecisionRecord` types the shape
    """Build one telemetry record for a Switch-LLM firing (switch / stay / gated).

    Persisted (via task_output → lid_shadow_events.lid_detection_events JSONB) for
    accuracy/latency metrics. Pure + module-level so the record contract is unit-tested
    independently of TaskManager. `flow=llm_switch` discriminates these from the legacy
    heuristic shape that shares the column.
    """
    dec = decision or {}
    return {
        "flow": "llm_switch",
        "fired_at": fired_at,
        "decide_latency_ms": round((now - fired_at) * 1000, 1),
        "path": "turn_boundary" if active_transcript else "idle_flush",
        "active_language": active,
        "detector_transcript": detector_transcript,
        "detector_lang_tag": detector_lang_tag,
        # Per-segment detections (a turn can span languages); the tag above is just the latest.
        "detector_lang_confidence": detector_lang_confidence,
        "detector_segments": detector_segments or [],
        "active_transcript": active_transcript,
        # What the caller is speaking, independent of support (set even when staying).
        "detected_language": dec.get("detected_language"),
        "detection_confidence": dec.get("detection_confidence"),
        "target_language": dec.get("target_language"),
        "target_confidence": dec.get("target_confidence"),
        "explicit_request": dec.get("explicit_request"),
        # Explicit-only judge fields; None on the ambient prompt.
        "request_status": dec.get("request_status"),
        "request_source": dec.get("request_source"),
        "reasoning": (dec.get("reasoning") or "").strip(),
        "buffered_max_segment_s": round(buffered_max_segment_s, 3),
        "speculation_started": speculation_started,
        "outcome": outcome,
        "switched_to": switched_to,
        "context_note_sent": context_note,
        "context_note_sent_at": now if context_note else None,
        # Old-language response state when this firing resolved — for a switch, whether
        "inflight_activity": inflight_activity or {},
    }


def asr_id_to_int(value: int | str | None) -> int | None:
    """Coerce OpenAI's "turn_3" ASR ids to int (Deepgram's are already ints); unparseable -> None."""
    if isinstance(value, str):
        digits = "".join(filter(str.isdigit, value))
        return int(digits) if digits else None
    return value


def trailing_utterance_text(
    segments: list[dict[str, Any]] | None,  # why: free-form detector segment dicts
    gap_seconds: float = 4.0,
) -> str:
    """Text of the caller's LAST utterance: trailing detector segments in the same
    language, back to a real silence boundary. Breaks on either a gap > gap_seconds
    (4s ≈ an utterance boundary, so a request that pauses to think is kept together,
    unlike a mid-sentence 1.5s) OR a language change (a distinct/stale prior utterance).
    Subtractive; returns '' so callers fall back to the full transcript."""
    tail = []
    prev_start = None
    tail_lang = None
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
