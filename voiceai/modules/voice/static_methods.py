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
from voiceai.modules.voice.constants import (
    INDIA_COUNTRY_CODE,
    INDIA_FULL_LENGTH,
    RECIPIENT_MAX_DIGITS,
    RECIPIENT_MIN_DIGITS,
)

__all__ = [
    "_inject_end_call_tool",
    "asr_id_to_int",
    "build_lid_decision_record",
    "is_alphanumeric_readout",
    "normalize_did_digits",
    "normalize_did_list",
    "trailing_utterance_text",
    "validate_recipient_number",
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


def normalize_did_digits(raw: str | None) -> str | None:
    """Strip a DID to digits-only (talko-service demands 10-15 digits).

    Deterministic and I/O-free: E.164 `+`, spaces and dashes vanish; empty or
    digitless input yields `None` so callers fail closed on missing DIDs.

    Args:
        raw: The DID in any common format, or `None`.

    Returns:
        The digits, or `None` when there is nothing dialable.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits or None


def validate_recipient_number(to_number: str) -> str:
    """Return the dialable digits for a destination, or raise `ValueError`.

    Deterministic and I/O-free: 10–15 digits overall; Indian (`91…`) numbers
    must be exactly 91 + 10 digits (an 11-digit `91…` value is almost always a
    dropped-digit typo, and Tata rejects it with an opaque BAD_REQUEST).

    Args:
        to_number: The destination in any common format.

    Returns:
        The digits-only destination.

    Raises:
        ValueError: When the destination is not dialable; the service converts
            this to `PlaceCallError` (never leaks across layers raw).
    """
    digits = re.sub(r"\D", "", to_number or "")
    if not RECIPIENT_MIN_DIGITS <= len(digits) <= RECIPIENT_MAX_DIGITS:
        raise ValueError(
            f"to_number {to_number!r} is not dialable: "
            f"need {RECIPIENT_MIN_DIGITS}-{RECIPIENT_MAX_DIGITS} digits, got {len(digits)}."
        )
    if digits.startswith(INDIA_COUNTRY_CODE) and len(digits) != INDIA_FULL_LENGTH:
        raise ValueError(
            f"to_number {to_number!r} looks like a truncated Indian mobile: need {INDIA_COUNTRY_CODE} + 10 digits."
        )
    return digits


def normalize_did_list(raw: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize DID lists to unique digits, preserving order (spec 0009).

    Deterministic and I/O-free; blanks and digitless entries vanish.

    Args:
        raw: DIDs in any common format, or `None`.

    Returns:
        The normalized unique digits.
    """
    seen: set[str] = set()
    out: list[str] = []
    for entry in raw or []:
        digits = normalize_did_digits(entry)
        if digits and digits not in seen:
            seen.add(digits)
            out.append(digits)
    return out


# --- Inbound screening primitives (spec 0047, Slice C) -------------------------------
# Pure, I/O-free call-screening steps for the Twilio ingress path. Screening order is
# fixed by Decision 3 (blocklist -> spam protection -> caller-match enrichment ->
# greeting resolution); every step returns a record-shaped outcome for the call
# record's additive `inbound_screening {decision, reason}` field (Decision 5).
# Nothing here touches a store, the network, or the clock, and nothing here logs:
# caller numbers are PII and stay at identifiers-only level in the layers that do
# log (AGENTS.md section 4). The api caller-match fetch is INJECTED by the
# composition layer, which also owns SSRF validation
# (`common.security.is_safe_outbound_url`, DNS-backed and hence not doable here)
# and timeout honoring — this module only guarantees it never invokes the fetch
# without a present, finite, positive timeout, and that every failure resolves
# fail-OPEN with a recorded reason (Decision 6: a down enrichment service must not
# drop calls). Catching `Exception` without re-raising below is that contract, not
# a Rule 8 lapse: `CancelledError` (a BaseException) still propagates, and reasons
# carry stable codes only — never `str(exc)` — so internals stay out of the record.
# Literals live as block-level constants below: `constants.py` is outside this
# slice's file ownership, so they are defined once here instead of scattered
# (reported loudly to the integrator for a follow-up move).

_E164_STRIP_RE = re.compile(r"[\s\-().]")
_E164_MIN_DIGITS = 7
_E164_MAX_DIGITS = 15

_MATCHED_KEY = "matched"
_CONTEXT_KEY = "context"
_REASON_KEY = "reason"
_DECISION_KEY = "decision"

_SCREEN_DECISION = "passed"

_SPAM_DISABLED_REASON = "spam_protection_disabled"
_SPAM_UNAVAILABLE_REASON = "spam_engine_unavailable"

_SOURCE_NONE = "none"
_SOURCE_CSV = "csv"
_SOURCE_SHEETS = "sheets"
_SOURCE_API = "api"
_OFFLINE_SOURCES = (_SOURCE_CSV, _SOURCE_SHEETS)

_SOURCE_NONE_REASON = "source_none"
_UNKNOWN_SOURCE_REASON = "unknown_source"
_CALLER_UNPARSEABLE_REASON = "caller_unparseable"
_REF_MISSING_REASON = "ref_missing"
_REF_UNSUPPORTED_REASON = "ref_unsupported"
_NO_MATCH_REASON = "no_match"
_MATCH_FOUND_REASON = "match_found"
_FETCH_MISSING_REASON = "fetch_missing"
_TIMEOUT_INVALID_REASON = "timeout_invalid"
_UNSAFE_URL_REASON = "unsafe_url"
_FETCH_TIMEOUT_REASON = "fetch_timeout"
_FETCH_ERROR_REASON = "fetch_error"

_HTTP_PREFIXES = ("http://", "https://")


def _normalize_e164(raw: str | None) -> str | None:
    """Normalize a phone number to E.164 (`+` + 7-15 digits); unparseable -> None."""
    if not raw or not isinstance(raw, str):
        return None
    compact = _E164_STRIP_RE.sub("", raw)
    if not compact.startswith("+"):
        return None
    digits = compact[1:]
    if not digits.isdigit() or not _E164_MIN_DIGITS <= len(digits) <= _E164_MAX_DIGITS:
        return None
    return "+" + digits


def _is_valid_timeout(timeout_s: float | None) -> bool:
    """Report whether a timeout is present, numeric, finite, and positive."""
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        return False
    return timeout_s > 0 and timeout_s < float("inf")


def _looks_like_http_url(url: str) -> bool:
    """Syntactic http(s)+host sanity check; NOT a substitute for `is_safe_outbound_url`."""
    lowered = url.strip().lower()
    return any(lowered.startswith(prefix) and len(lowered) > len(prefix) for prefix in _HTTP_PREFIXES)


def is_blocklisted(caller: str | None, blocklist: Iterable[str] | None) -> bool:
    """Report whether an inbound caller is on the blocklist (screening step 1).

    Both sides are E.164-normalized before comparison, so formatting drift between
    the carrier `From` value and stored rows can neither block the wrong caller nor
    let a blocked one through on a dash. Unparseable values never match.

    Args:
        caller: The carrier-supplied caller id in any common format.
        blocklist: Stored blocklist rows in any common format, or `None`.

    Returns:
        `True` only when the normalized caller equals a normalized entry; `False`
        for missing/unparseable callers and empty blocklists (fail-open: screening
        never rejects what it cannot parse — rejection on a positive match is
        Slice B's job).
    """
    normalized = _normalize_e164(caller)
    if normalized is None:
        return False
    for entry in blocklist or []:
        if isinstance(entry, str) and _normalize_e164(entry) == normalized:
            return True
    return False


def spam_verdict(caller: str | None, spam_protection: bool) -> dict[str, str]:
    """Stub spam gate honoring the `spam_protection` flag (screening step 2).

    HONEST CAPABILITY: there is no spam engine in this repo — no ML model, no
    carrier spam-header consumer, no reputation list. This stub therefore NEVER
    reports spam: flag off answers the documented pass-through, flag on answers
    pass with an `unavailable` reason so the call record shows the operator asked
    for protection that does not exist yet. Wiring a real signal (Twilio SpamRisk,
    STIR/SHAKEN verstat, reputation lookup) is a follow-up spec; until then any
    "spam blocked" claim would be fabricated.

    Args:
        caller: Accepted for pipeline uniformity and forward compatibility. It does
            not influence the stub verdict and is never inspected (identifiers-only
            PII posture — this layer logs nothing at all).
        spam_protection: The inbound config flag.

    Returns:
        `{"decision": "passed", "reason": ...}` with reason
        `spam_protection_disabled` (flag off) or `spam_engine_unavailable`
        (flag on).
    """
    if not spam_protection:
        return {_DECISION_KEY: _SCREEN_DECISION, _REASON_KEY: _SPAM_DISABLED_REASON}
    return {_DECISION_KEY: _SCREEN_DECISION, _REASON_KEY: _SPAM_UNAVAILABLE_REASON}


def _match_offline(
    ref: str | Iterable[str] | dict[str, Any] | None,  # why: caller-match stores are free-form legacy rows
    caller: str,
) -> dict[str, Any]:  # why: the matched context mirrors the free-form caller-match store row
    """Match a normalized caller against csv/sheets reference data (fail-open)."""
    if ref is None or (isinstance(ref, str) and not ref.strip()):
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _REF_MISSING_REASON}
    if isinstance(ref, str):
        entries: Iterable[Any] = [ref]  # why: a lone string is one candidate number, not char soup
    elif isinstance(ref, dict):
        for key, value in ref.items():
            if isinstance(key, str) and _normalize_e164(key) == caller:
                return {_MATCHED_KEY: True, _CONTEXT_KEY: value, _REASON_KEY: _MATCH_FOUND_REASON}
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _NO_MATCH_REASON}
    elif isinstance(ref, Iterable):
        entries = ref
    else:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _REF_UNSUPPORTED_REASON}
    saw_row = False
    for entry in entries:
        if isinstance(entry, dict):
            saw_row = True  # sheets row-shape (list of mappings): schema unpinned, reported honestly below
            continue
        if isinstance(entry, str) and _normalize_e164(entry) == caller:
            return {_MATCHED_KEY: True, _CONTEXT_KEY: None, _REASON_KEY: _MATCH_FOUND_REASON}
    if saw_row:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _REF_UNSUPPORTED_REASON}
    return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _NO_MATCH_REASON}


def _match_via_api(
    ref: str | Iterable[str] | dict[str, Any] | None,  # why: caller-match stores are free-form legacy rows
    caller: str,
    fetch: Any | None,  # why: the injected api fetch is an untyped seam; invoked as fetch(url, caller, timeout_s)
    timeout_s: float | None,
) -> dict[str, Any]:  # why: the matched context mirrors the free-form caller-match store row
    """Match via the injected api fetch: timeout mandatory, every failure fail-open."""
    if not isinstance(ref, str) or not ref.strip():
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _REF_MISSING_REASON}
    url = ref.strip()
    if not _looks_like_http_url(url):
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _UNSAFE_URL_REASON}
    if fetch is None:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _FETCH_MISSING_REASON}
    if not _is_valid_timeout(timeout_s):
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _TIMEOUT_INVALID_REASON}
    try:
        payload = fetch(url, caller, timeout_s)
    except TimeoutError:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _FETCH_TIMEOUT_REASON}
    except Exception:  # Decision 6 fail-open: a down enrichment service must not drop calls
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _FETCH_ERROR_REASON}
    if not payload:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _NO_MATCH_REASON}
    return {_MATCHED_KEY: True, _CONTEXT_KEY: payload, _REASON_KEY: _MATCH_FOUND_REASON}


def caller_match_context(
    source: str | None,
    ref: str | Iterable[str] | dict[str, Any] | None,  # why: caller-match stores are free-form legacy rows
    caller: str | None,
    fetch: Any | None = None,  # why: injected fetch is untyped; called as fetch(url, caller, timeout_s)
    timeout_s: float | None = None,
) -> dict[str, Any]:  # why: the matched context mirrors the free-form caller-match store row
    """Enrich an inbound caller from the configured match source (screening step 3).

    Sources `csv`/`sheets` match locally today (plain number lists, or number->context
    mappings whose value is returned as context); sheets list-of-mapping rows report
    `ref_unsupported` until their schema is pinned (open question for the
    integrator — a silent no-match would lie). Source `api` delegates to the
    injected `fetch(url, timeout_s)` callable with a mandatory timeout and resolves
    every failure fail-OPEN with a recorded reason. Unknown sources and unparseable
    callers fail open the same way. Always returns identifiers only, never payloads.

    Args:
        source: The `caller_match_source` value (`none`/`csv`/`sheets`/`api`,
            case-insensitive; missing or `none` disables enrichment).
        ref: The reference: candidate numbers or a number->context mapping for
            `csv`/`sheets`; the directory URL string for `api`.
        caller: The carrier-supplied caller id in any common format.
        fetch: Injected api fetch, invoked positionally as
            `fetch(url, caller, timeout_s)` with the E.164-normalized caller. It
            must honor the timeout internally and must only ever receive URLs
            the injector already cleared through
            `common.security.is_safe_outbound_url` (DNS-backed SSRF guard, not
            doable in this I/O-free layer).
        timeout_s: Mandatory bound for the api fetch; missing, non-numeric,
            non-positive, or infinite values fail open WITHOUT calling `fetch`.

    Returns:
        `{"matched": bool, "context": ..., "reason": str}` where context is the
        mapping value / api payload on a hit and `None` otherwise, and reason is a
        stable code (`source_none`, `match_found`, `no_match`,
        `caller_unparseable`, `ref_missing`, `ref_unsupported`, `unknown_source`,
        `fetch_missing`, `timeout_invalid`, `unsafe_url`, `fetch_timeout`,
        `fetch_error`).
    """
    normalized_source = (source or "").strip().lower()
    if not normalized_source or normalized_source == _SOURCE_NONE:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _SOURCE_NONE_REASON}
    normalized_caller = _normalize_e164(caller)
    if normalized_caller is None:
        return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _CALLER_UNPARSEABLE_REASON}
    if normalized_source in _OFFLINE_SOURCES:
        return _match_offline(ref, normalized_caller)
    if normalized_source == _SOURCE_API:
        return _match_via_api(ref, normalized_caller, fetch, timeout_s)
    return {_MATCHED_KEY: False, _CONTEXT_KEY: None, _REASON_KEY: _UNKNOWN_SOURCE_REASON}


def resolve_greeting(inbound_greeting: str | None, agent_welcome: str | None) -> str | None:
    """Resolve the spoken opening line (screening step 4, Decision 4).

    Precedence is a single rule — no merge, no chain: a SET inbound greeting wins
    over the agent welcome message; an UNSET one (`None`, empty, whitespace-only)
    leaves today's behavior unchanged by answering the agent welcome (which may
    itself be `None` when the agent has none configured).

    Args:
        inbound_greeting: The per-number `inbound.greeting` override, if configured.
        agent_welcome: The agent's `agent_welcome_message` (today's default).

    Returns:
        The inbound greeting verbatim when set, else the agent welcome verbatim.
    """
    if inbound_greeting is not None and inbound_greeting.strip():
        return inbound_greeting
    return agent_welcome
