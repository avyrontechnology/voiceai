"""Inbound screening primitives (spec 0047, Slice C): pure-function matrix.

Covers the four appended helpers in `voiceai.modules.voice.static_methods` —
blocklist matching with two-sided E.164 normalization, the honest spam stub, the
none/csv/sheets/api caller-match enrichment (api path timeout-bound and fail-open),
and Decision 4 greeting precedence. No I/O, no store, no network: the api fetch is
always an injected fake, and failure-path tests assert the composition contract
(the fetch is never invoked without a valid timeout, and every failure resolves
fail-open with a recorded reason).
"""

from __future__ import annotations

import pytest

from voiceai.modules.voice import static_methods as screening

CALLER = "+15550001111"
CALLER_DASHED = "+1-555-000-1111"
CALLER_SPACED_PARENS = "+1 (555) 000 1111"
CALLER_DOTTED = "+1.555.000.1111"
OTHER = "+15550002222"


# --- is_blocklisted -------------------------------------------------------------


def test_blocklisted_exact_match():
    """A stored entry identical to the caller blocks."""
    assert screening.is_blocklisted(CALLER, [OTHER, CALLER]) is True


def test_blocklisted_formatting_drift_matches_both_sides():
    """Dashes/parens/spaces/dots on either side normalize to the same E.164 value."""
    assert screening.is_blocklisted(CALLER_DASHED, [CALLER_SPACED_PARENS]) is True
    assert screening.is_blocklisted(CALLER_SPACED_PARENS, [CALLER_DOTTED]) is True
    assert screening.is_blocklisted(CALLER, [CALLER_DASHED]) is True


def test_not_blocklisted_passes():
    """An unlisted caller is not blocked."""
    assert screening.is_blocklisted(CALLER, [OTHER]) is False


def test_empty_and_missing_blocklist_pass():
    """Empty or missing blocklists never block (fail-open)."""
    assert screening.is_blocklisted(CALLER, []) is False
    assert screening.is_blocklisted(CALLER, None) is False


@pytest.mark.parametrize(
    "caller",
    [None, "", "   ", "garbage", "5550001111", "+12", "+1234567890123456", "+-()"],
)
def test_unparseable_caller_never_blocks(caller):
    """Missing, non-E.164, too-short, or too-long callers never match (fail-open)."""
    assert screening.is_blocklisted(caller, [CALLER, caller if isinstance(caller, str) else OTHER]) is False


def test_unparseable_entries_never_match():
    """Garbage rows in the blocklist are skipped, never matched."""
    assert screening.is_blocklisted(CALLER, ["garbage", "", OTHER]) is False


def test_entry_without_plus_never_matches():
    """A stored row without a leading `+` is unparseable and never blocks its twin."""
    assert screening.is_blocklisted(CALLER, ["15550001111"]) is False


# --- spam_verdict (honest stub) --------------------------------------------------


def test_spam_off_is_documented_pass_through():
    """Flag off answers the documented pass-through."""
    assert screening.spam_verdict(CALLER, False) == {"decision": "passed", "reason": "spam_protection_disabled"}


def test_spam_on_still_passes_engine_unavailable():
    """Flag on cannot block: no engine exists, so the record says `unavailable`."""
    assert screening.spam_verdict(CALLER, True) == {"decision": "passed", "reason": "spam_engine_unavailable"}


@pytest.mark.parametrize("flag", [False, True])
def test_spam_stub_never_reports_spam(flag):
    """The stub's whole vocabulary is `passed` — any block claim would be fabricated."""
    verdict = screening.spam_verdict(CALLER, flag)
    assert verdict["decision"] == "passed"
    assert verdict["decision"] not in {"blocked", "spam", "reject"}


# --- caller_match_context: source routing ---------------------------------------


@pytest.mark.parametrize("source", [None, "", "   ", "none", "  NONE  "])
def test_none_source_is_pass_through(source):
    """Missing/`none` sources disable enrichment without touching the fetch."""
    calls = []

    def fetch(url, caller, timeout_s):
        calls.append((url, caller, timeout_s))

    result = screening.caller_match_context(source, None, CALLER, fetch=fetch, timeout_s=5.0)
    assert result == {"matched": False, "context": None, "reason": "source_none"}
    assert calls == []


def test_unknown_source_fails_open():
    """An unrecognized source fails open with a recorded reason."""
    result = screening.caller_match_context("carrier-pigeon", [CALLER], CALLER)
    assert result == {"matched": False, "context": None, "reason": "unknown_source"}


@pytest.mark.parametrize("source", ["csv", "sheets", "api"])
def test_unparseable_caller_fails_open_without_fetch(source):
    """Unparseable callers fail open before any reference lookup or fetch."""
    calls = []

    def fetch(url, caller, timeout_s):
        calls.append((url, caller, timeout_s))
        return {"name": "Nobody"}

    result = screening.caller_match_context(
        source, [CALLER], "not-a-number", fetch=fetch, timeout_s=5.0
    )
    assert result == {"matched": False, "context": None, "reason": "caller_unparseable"}
    assert calls == []


# --- caller_match_context: csv/sheets -------------------------------------------


def test_csv_list_hit_and_miss_with_drift():
    """Plain number lists match across formatting drift; strangers miss."""
    hit = screening.caller_match_context("csv", [CALLER_DASHED], CALLER_SPACED_PARENS)
    assert hit == {"matched": True, "context": None, "reason": "match_found"}
    miss = screening.caller_match_context("CSV", [OTHER], CALLER)
    assert miss == {"matched": False, "context": None, "reason": "no_match"}


def test_csv_mapping_hit_returns_context():
    """Number->context mappings return the stored context on a hit."""
    hit = screening.caller_match_context("csv", {CALLER_DASHED: "VIP caller"}, CALLER)
    assert hit == {"matched": True, "context": "VIP caller", "reason": "match_found"}
    miss = screening.caller_match_context("csv", {OTHER: "Stranger"}, CALLER)
    assert miss == {"matched": False, "context": None, "reason": "no_match"}


@pytest.mark.parametrize("ref", [None, "", "   "])
def test_csv_missing_ref_fails_open(ref):
    """A missing reference fails open with a recorded reason."""
    result = screening.caller_match_context("csv", ref, CALLER)
    assert result == {"matched": False, "context": None, "reason": "ref_missing"}


def test_csv_unsupported_ref_fails_open():
    """A non-collection reference fails open instead of raising."""
    result = screening.caller_match_context("csv", 123, CALLER)  # type: ignore[arg-type]  # deliberate wrong-type probe
    assert result == {"matched": False, "context": None, "reason": "ref_unsupported"}


def test_sheets_string_list_matches_like_csv():
    """Sheets string exports share the offline matching path."""
    result = screening.caller_match_context("sheets", [CALLER_DOTTED], CALLER)
    assert result == {"matched": True, "context": None, "reason": "match_found"}


def test_sheets_row_mappings_report_unsupported():
    """Sheets list-of-mapping rows report `ref_unsupported`: the schema is unpinned."""
    rows = [{"phone": CALLER, "name": "Ada"}]
    result = screening.caller_match_context("sheets", rows, CALLER)  # type: ignore[arg-type]  # unpinned row schema probe
    assert result == {"matched": False, "context": None, "reason": "ref_unsupported"}


def test_csv_empty_list_is_no_match():
    """A present-but-empty collection is a miss, not a missing reference."""
    result = screening.caller_match_context("csv", [], CALLER)
    assert result == {"matched": False, "context": None, "reason": "no_match"}


# --- caller_match_context: api ---------------------------------------------------


def test_api_hit_returns_payload_and_forwards_timeout():
    """The fetch receives (url, normalized caller, timeout) and its payload is context."""
    seen = []

    def fetch(url, caller, timeout_s):
        seen.append((url, caller, timeout_s))
        return {"name": "Ada"}

    result = screening.caller_match_context(
        "api", "https://crm.example.com/directory", CALLER_DASHED, fetch=fetch, timeout_s=5.0
    )
    assert result == {"matched": True, "context": {"name": "Ada"}, "reason": "match_found"}
    assert seen == [("https://crm.example.com/directory", CALLER, 5.0)]


@pytest.mark.parametrize("payload", [None, "", {}, [], False])
def test_api_empty_payload_is_no_match(payload):
    """Falsy fetch payloads are misses, not errors."""

    def fetch(url, caller, timeout_s):
        return payload

    result = screening.caller_match_context(
        "api", "https://crm.example.com/directory", CALLER, fetch=fetch, timeout_s=5.0
    )
    assert result == {"matched": False, "context": None, "reason": "no_match"}


def test_api_missing_fetch_fails_open():
    """No injected fetch fails open without raising."""
    result = screening.caller_match_context(
        "api", "https://crm.example.com/directory", CALLER, fetch=None, timeout_s=5.0
    )
    assert result == {"matched": False, "context": None, "reason": "fetch_missing"}


@pytest.mark.parametrize("timeout_s", [None, 0, 0.0, -1, float("nan"), float("inf"), "5", True])
def test_api_invalid_timeout_fails_open_without_call(timeout_s):
    """Missing/non-numeric/non-positive/non-finite timeouts fail open; fetch untouched."""
    calls = []

    def fetch(url, caller, timeout):
        calls.append((url, caller, timeout))
        return {"name": "Ada"}

    result = screening.caller_match_context(
        "api", "https://crm.example.com/directory", CALLER, fetch=fetch, timeout_s=timeout_s
    )
    assert result == {"matched": False, "context": None, "reason": "timeout_invalid"}
    assert calls == []


def test_api_timeout_error_fails_open():
    """A fetch timeout fails open with its own recorded reason (Decision 6)."""

    def fetch(url, caller, timeout_s):
        raise TimeoutError("slow directory")

    result = screening.caller_match_context(
        "api", "https://crm.example.com/directory", CALLER, fetch=fetch, timeout_s=5.0
    )
    assert result == {"matched": False, "context": None, "reason": "fetch_timeout"}


def test_api_fetch_error_fails_open_opaquely():
    """Fetch crashes fail open with a stable code — never the exception text (§4)."""

    def fetch(url, caller, timeout_s):
        raise RuntimeError("connection refused: db credential leak hint")

    result = screening.caller_match_context(
        "api", "https://crm.example.com/directory", CALLER, fetch=fetch, timeout_s=5.0
    )
    assert result == {"matched": False, "context": None, "reason": "fetch_error"}


@pytest.mark.parametrize("url", ["ftp://crm.example.com/dir", "file:///etc/passwd", "not a url"])
def test_api_unsafe_url_fails_open_without_call(url):
    """Non-http(s) references fail open; the fetch is never invoked."""
    calls = []

    def fetch(url, caller, timeout_s):
        calls.append((url, caller, timeout_s))
        return {"name": "Ada"}

    result = screening.caller_match_context("api", url, CALLER, fetch=fetch, timeout_s=5.0)
    assert result == {"matched": False, "context": None, "reason": "unsafe_url"}
    assert calls == []


@pytest.mark.parametrize("ref", [None, "", "   "])
def test_api_missing_url_ref_fails_open(ref):
    """A missing directory URL fails open with a recorded reason."""
    result = screening.caller_match_context("api", ref, CALLER, fetch=lambda *a: None, timeout_s=5.0)
    assert result == {"matched": False, "context": None, "reason": "ref_missing"}


# --- resolve_greeting (Decision 4) ------------------------------------------------


def test_set_inbound_greeting_wins():
    """A set inbound greeting wins over the agent welcome verbatim (no merge)."""
    assert screening.resolve_greeting("Number override", "Agent welcome") == "Number override"


def test_inbound_wins_even_when_welcome_missing():
    """The override also wins when the agent has no welcome configured."""
    assert screening.resolve_greeting("Number override", None) == "Number override"


@pytest.mark.parametrize("inbound", [None, "", "   "])
def test_unset_inbound_falls_back_to_welcome(inbound):
    """Unset (None/empty/whitespace) inbound greetings keep today's welcome behavior."""
    assert screening.resolve_greeting(inbound, "Agent welcome") == "Agent welcome"


def test_both_missing_answers_none():
    """No override and no welcome resolves to no greeting."""
    assert screening.resolve_greeting(None, None) is None
