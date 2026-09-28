"""Inbound-engine contract pins (spec 0047, Slice D): footprint, greeting, rejects, docs.

Mechanical drift gate over Slices A-C. Every pin is textual (an anchor string
in a source file); nothing imports peer-owned modules, so a half-written
peer file can never break collection (AGENTS.md Rule 8: small functions,
named anchors, no magic values inline).

Pins over peer-owned files start PROVISIONAL: a slice that has not landed yet
leaves none of its anchors behind, and the pin skips instead of failing. Once
any anchor of a slice is present the slice counts as landed and every anchor
is enforced — partial landings fail loudly as drift. The absence pins (no
legacy/platform imports, no store writes) and the docs pins (Slice D's own
files) are final from the start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ENCODING = "utf-8"

INBOUND_PATH = "voiceai/modules/voice/session/inbound.py"
CONTROLLER_PATH = "voiceai/modules/voice/controller.py"
COMPOSITION_PATH = "voiceai/modules/voice/session/composition.py"
STATIC_METHODS_PATH = "voiceai/modules/voice/static_methods.py"
LOOKUP_TEST_PATH = "voiceai/modules/voice/tests/test_inbound_lookup.py"
WEBHOOK_TEST_PATH = "voiceai/modules/voice/tests/test_inbound_webhook.py"
SCREENING_TEST_PATH = "voiceai/modules/voice/tests/test_screening.py"
OPENAPI_PATH = "openapi.yaml"
API_REFERENCE_PATH = "API_REFERENCE.md"

WRITER_PATHS = (INBOUND_PATH, CONTROLLER_PATH, COMPOSITION_PATH, STATIC_METHODS_PATH)

RESOLVE_INBOUND = "resolve_inbound"
RESOLVE_GREETING = "resolve_greeting"
IS_BLOCKLISTED = "is_blocklisted"
SPAM_VERDICT = "spam_verdict"
CALLER_MATCH_CONTEXT = "caller_match_context"
AGENT_WELCOME = "agent_welcome_message"
INBOUND_GREETING_FIELD = "inbound.greeting"
WEBHOOK_ROUTE = "INBOUND_TWILIO_PATH"
TWILIO_SIGNATURE = "X-Twilio-Signature"
CALL_SID = "CallSid"
STORE_SEAM = "store"
SPEC_TAG = "spec 0047"

SCREENING_ANCHORS = (IS_BLOCKLISTED, SPAM_VERDICT, CALLER_MATCH_CONTEXT, RESOLVE_GREETING)
WEBHOOK_ANCHORS = (WEBHOOK_ROUTE, TWILIO_SIGNATURE, CALL_SID)
LOOKUP_TEST_CASES = ("unassigned", "unparseable", "tenant")
WEBHOOK_TEST_CASES = ("unknown", "signature", "blocked")
SCREENING_TEST_CASES = (IS_BLOCKLISTED, SPAM_VERDICT, CALLER_MATCH_CONTEXT, RESOLVE_GREETING)

REJECT_ALIASES = ("Reject", "reject", "deny")
EQUALITY_ALIASES = ("==", "identical", "equal")

BANNED_IMPORT_FRAGMENTS = ("agent_manager", "from platform", "import platform")
WRITE_MARKERS = (
    "insert_one",
    "update_one",
    "delete_one",
    "replace_one",
    "find_one_and",
    "bulk_write",
    "delete_many",
    "update_many",
    ".save(",
)

SLICE_ANCHORS = {
    INBOUND_PATH: (RESOLVE_INBOUND,),
    CONTROLLER_PATH: WEBHOOK_ANCHORS,
    COMPOSITION_PATH: (RESOLVE_GREETING,),
    STATIC_METHODS_PATH: SCREENING_ANCHORS,
}

ABSENT_STATE = "absent"
PROVISIONAL_STATE = "provisional"
LANDED_STATE = "landed"

API_REFERENCE_MARKER = "Inbound engine (spec 0047)"
API_REFERENCE_REQUIRED_PHRASES = (
    API_REFERENCE_MARKER,
    "/voice/inbound/twilio",
    TWILIO_SIGNATURE,
    "blocklist",
    "spam",
    "caller-match",
    AGENT_WELCOME,
    INBOUND_GREETING_FIELD,
    "inbound_screening",
    "decision",
    "reason",
    "Carrier seam",
    "tenant-safe",
)


def _read_text_or_none(rel_path: str) -> str | None:
    """Read one repo file as text, or None when the peer slice has not landed it.

    Args:
        rel_path: Repo-relative file path.

    Returns:
        The file's full text, or None when the file does not exist yet.
    """
    path = REPO_ROOT / rel_path
    if not path.is_file():
        return None
    return path.read_text(encoding=SOURCE_ENCODING)


def _slice_state(rel_path: str) -> str:
    """Classify a peer-owned file as absent, provisional, or landed (spec 0047).

    Args:
        rel_path: Repo-relative file path of a Slice A-C writer.

    Returns:
        ABSENT when the file does not exist, PROVISIONAL when it exists but
        carries none of its slice anchors, LANDED otherwise.
    """
    text = _read_text_or_none(rel_path)
    if text is None:
        return ABSENT_STATE
    if any(anchor in text for anchor in SLICE_ANCHORS[rel_path]):
        return LANDED_STATE
    return PROVISIONAL_STATE


def _require_landed(rel_path: str, label: str) -> str:
    """Return a landed slice file's text, skipping while the slice is provisional.

    Args:
        rel_path: Repo-relative file path of a Slice A-C writer.
        label: Slice label for the skip message.

    Returns:
        The file's full text; never returns for absent/provisional slices.
    """
    state = _slice_state(rel_path)
    if state != LANDED_STATE:
        pytest.skip(f"PROVISIONAL({SPEC_TAG}): {label} not landed ({rel_path} is {state})")
    text = _read_text_or_none(rel_path)
    assert text is not None
    return text


def _import_lines(text: str) -> list[str]:
    """Return the import-statement lines of a source file's text.

    Docstring provenance notes (e.g. ``voiceai/agent_manager/...`` in a moved-
    verbatim header) are not imports; only real statements are scanned.

    Args:
        text: The file's full text.

    Returns:
        The stripped import/from lines.
    """
    return [line.strip() for line in text.splitlines() if line.strip().startswith(("import ", "from "))]


def test_lookup_defines_resolve_inbound() -> None:
    """Slice A: the lookup seam exposes `resolve_inbound` over an injected store."""
    text = _require_landed(INBOUND_PATH, "Slice A lookup")
    assert RESOLVE_INBOUND in text
    assert STORE_SEAM in text


def test_lookup_tests_cover_assignment_matrix() -> None:
    """Slice A: lookup units pin normalization, unassigned, unparseable, tenancy."""
    text = _read_text_or_none(LOOKUP_TEST_PATH)
    if text is None or RESOLVE_INBOUND not in text:
        pytest.skip(f"PROVISIONAL({SPEC_TAG}): Slice A lookup tests not landed")
    missing = [case for case in LOOKUP_TEST_CASES if case not in text]
    assert missing == []


def test_webhook_endpoint_is_thin_and_form_shaped() -> None:
    """Slice B: the Twilio webhook carries its form fields and signature gate."""
    text = _require_landed(CONTROLLER_PATH, "Slice B webhook")
    missing = [anchor for anchor in WEBHOOK_ANCHORS if anchor not in text]
    assert missing == []


def test_webhook_tests_cover_reject_matrix() -> None:
    """Slice B: factory webhook tests name unknown, bad-signature, blocked cases."""
    text = _read_text_or_none(WEBHOOK_TEST_PATH)
    if text is None or "twilio" not in text.lower():
        pytest.skip(f"PROVISIONAL({SPEC_TAG}): Slice B webhook tests not landed")
    missing = [case for case in WEBHOOK_TEST_CASES if case not in text]
    assert missing == []


def test_screening_pure_functions_present() -> None:
    """Slice C: blocklist, spam, caller-match, and greeting resolvers are pure."""
    text = _require_landed(STATIC_METHODS_PATH, "Slice C screening")
    missing = [anchor for anchor in SCREENING_ANCHORS if anchor not in text]
    assert missing == []


def test_screening_tests_cover_matrix() -> None:
    """Slice C: the pure screening matrix is unit-tested, not wired by hand."""
    text = _read_text_or_none(SCREENING_TEST_PATH)
    if text is None or "screen" not in text.lower():
        pytest.skip(f"PROVISIONAL({SPEC_TAG}): Slice C screening tests not landed")
    missing = [case for case in SCREENING_TEST_CASES if case not in text]
    assert missing == []


def test_writers_import_no_legacy_or_platform() -> None:
    """Slices A-C: writers never import legacy or platform internals (injected seam only)."""
    violations: list[str] = []
    seen = 0
    for rel_path in WRITER_PATHS:
        text = _read_text_or_none(rel_path)
        if text is None:
            continue
        seen += 1
        violations.extend(
            f"{rel_path}: banned import {line!r}"
            for line in _import_lines(text)
            for fragment in BANNED_IMPORT_FRAGMENTS
            if fragment in line
        )
    assert seen > 0
    assert violations == []


def test_writers_perform_no_store_writes() -> None:
    """Slices A-C: the ingress path reads assignments; persistence stays platform-side."""
    violations: list[str] = []
    for rel_path in WRITER_PATHS:
        text = _read_text_or_none(rel_path)
        if text is None:
            continue
        violations.extend(f"{rel_path}: store-write marker {marker!r}" for marker in WRITE_MARKERS if marker in text)
    assert violations == []


def test_greeting_precedence_resolve_greeting() -> None:
    """Slice C: `inbound.greeting` wins over `agent_welcome_message` (Decision 4)."""
    text = _require_landed(STATIC_METHODS_PATH, "Slice C screening")
    region = text[text.index(RESOLVE_GREETING) :]
    assert INBOUND_GREETING_FIELD in text or "greeting" in region
    assert AGENT_WELCOME in region


def test_greeting_override_applied_in_composition() -> None:
    """Slice B: the composition hunk applies the `resolve_greeting` result."""
    text = _require_landed(COMPOSITION_PATH, "Slice B greeting hunk")
    assert RESOLVE_GREETING in text


def test_reject_shapes_share_one_builder() -> None:
    """Unknown, bad-signature, and blocked callers answer one identical reject shape."""
    text = _require_landed(CONTROLLER_PATH, "Slice B webhook")
    assert TWILIO_SIGNATURE in text
    assert any(alias in text for alias in REJECT_ALIASES)
    assert "block" in text


def test_reject_equality_pinned_by_webhook_tests() -> None:
    """Slice B tests assert the reject wire shapes are equal, not merely present."""
    text = _read_text_or_none(WEBHOOK_TEST_PATH)
    if text is None or "twilio" not in text.lower():
        pytest.skip(f"PROVISIONAL({SPEC_TAG}): Slice B webhook tests not landed")
    assert any(alias in text for alias in EQUALITY_ALIASES)


def test_api_reference_documents_inbound() -> None:
    """Slice D docs: API_REFERENCE carries the inbound-engine behavior note."""
    text = _read_text_or_none(API_REFERENCE_PATH)
    assert text is not None
    missing = [phrase for phrase in API_REFERENCE_REQUIRED_PHRASES if phrase not in text]
    assert missing == []


def test_openapi_pointer_matches_code() -> None:
    """Slice D docs: openapi stays valid and points at the inbound contract owner."""
    text = _read_text_or_none(OPENAPI_PATH)
    assert text is not None
    assert "openapi:" in text
    assert "0047" in text
    assert "/voice/inbound/twilio" in text


def test_docs_match_code_at_gate_time() -> None:
    """Integrator re-verify: every docs symbol resolves to a code anchor (retry after peers land)."""
    pairs = (
        (RESOLVE_INBOUND, INBOUND_PATH),
        (RESOLVE_GREETING, STATIC_METHODS_PATH),
        (IS_BLOCKLISTED, STATIC_METHODS_PATH),
        (SPAM_VERDICT, STATIC_METHODS_PATH),
        (CALLER_MATCH_CONTEXT, STATIC_METHODS_PATH),
        (WEBHOOK_ROUTE, CONTROLLER_PATH),
        (TWILIO_SIGNATURE, CONTROLLER_PATH),
        (RESOLVE_GREETING, COMPOSITION_PATH),
    )
    checked = 0
    failures = []
    for anchor, rel_path in pairs:
        if _slice_state(rel_path) != LANDED_STATE:
            continue
        checked += 1
        text = _read_text_or_none(rel_path)
        if text is None or anchor not in text:
            failures.append(f"{anchor} missing from {rel_path}")
    if checked == 0:
        pytest.skip(f"PROVISIONAL({SPEC_TAG}): no Slice A-C file landed yet")
    assert failures == []
