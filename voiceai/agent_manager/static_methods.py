"""Pure builders for the agent_manager package.

Deterministic constructors from validated inputs: no TaskManager state,
no store, no I/O. Services supply context explicitly so the builders stay
trivially unit-testable.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Optional

from voiceai.constants import END_CALL_FUNCTION_PREFIX, END_CALL_TOOL_DEFINITION

__all__ = ["build_lid_decision_record", "inject_end_call_tool"]


def inject_end_call_tool(
    api_tools: Optional[Dict[str, Any]],
    *,
    scope: Any,
    nodes: Any,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Add the internal end_call tool (with scope/nodes) to api_tools.

    Args:
        api_tools: Tool registry dict (created when None).
        scope: Tool scope (`.value` used when present).
        nodes: Handoff nodes list.
        description: Optional description override.

    Returns:
        The registry with end_call injected (no-op if already present).
    """
    if api_tools is None:
        api_tools = {"tools": [], "tools_params": {}}
    if END_CALL_FUNCTION_PREFIX in api_tools.get("tools_params", {}):
        return api_tools  # already injected by the experiment path or a prior call
    tool_def: Dict[str, Any] = copy.deepcopy(END_CALL_TOOL_DEFINITION)
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


def build_lid_decision_record(
    *,
    outcome: str,
    fired_at: float,
    now: float,
    active_transcript: Any,
    active: Any,
    detector_transcript: Any,
    detector_lang_tag: Any,
    decision: Any,
    buffered_max_segment_s: float,
    speculation_started: Any,
    switched_to: Optional[str] = None,
    context_note: Any = None,
    detector_lang_confidence: Any = None,
    detector_segments: Any = None,
    inflight_activity: Any = None,
) -> Dict[str, Any]:
    """Build one telemetry record for a Switch-LLM firing (switch / stay / gated).

    Persisted (via task_output → lid_shadow_events.lid_detection_events JSONB) for
    accuracy/latency metrics. Pure + module-level so the record contract is unit-tested
    independently of TaskManager. `flow=llm_switch` discriminates these from the legacy
    heuristic shape that shares the column.

    Args:
        outcome: Firing outcome label.
        fired_at: Epoch seconds when the decide started.
        now: Epoch seconds at record build.
        active_transcript: Current-language transcript.
        active: Active language tag.
        detector_transcript: Detector transcript.
        detector_lang_tag: Latest detector tag.
        decision: Judge decision dict.
        buffered_max_segment_s: Longest buffered segment.
        speculation_started: Speculation flag/state.
        switched_to: Target language on switch.
        context_note: Context note if sent.
        detector_lang_confidence: Detector confidence.
        detector_segments: Per-segment detections.
        inflight_activity: Old-language response state.

    Returns:
        The telemetry record dict.
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
