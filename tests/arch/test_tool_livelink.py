"""Shared-tool live-link pins (spec 0046, Slice D; integrator-closed): propagation, hard stops, parity, seed, docs.

Mechanical drift gate over Slices A-C. Every pin is textual (an anchor string
in a source file); nothing imports peer-owned modules, so a half-written
peer file can never break collection (AGENTS.md Rule 8: small functions,
named anchors, no magic values inline).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ENCODING = "utf-8"

TOOLS_SERVICE_PATH = "voiceai/modules/tools/service.py"
TOOLS_ERRORS_PATH = "voiceai/modules/tools/errors.py"
TOOLS_SEED_PATH = "voiceai/modules/tools/seed.py"
AGENTS_SERVICE_PATH = "voiceai/modules/agents/service.py"
AGENTS_SERVICE_TOOLS_PATH = "voiceai/modules/agents/service_tools.py"
TOOLS_CONTRACT_PATH = "voiceai/modules/tools/CONTRACT.md"
OPENAPI_PATH = "openapi.yaml"
API_REFERENCE_PATH = "API_REFERENCE.md"

UPDATE_TOOL_DEF = "    async def update_tool"
DELETE_TOOL_DEF = "    async def delete_tool"
BLOCK_END_MARKERS = ("\n    async def ", "\n    def ", "\nclass ")

VERSION_ANCHOR = "tools_version"
PROPAGATION_ANCHOR = "propagat"
REFERENCE_ANCHOR = "referenc"
CONFLICT_ANCHORS = ("409", "CONFLICT")
DEPRECATION_ANCHOR = "deprecated"
STALE_FLAG = "stale_deprecated"
TOOL_REFS_ANCHOR = "tool_refs"
SSRF_SEAM = "_is_url_safe"
SSRF_GATE = "is_safe_outbound_url"
RE_MATERIALIZE_ALIASES = ("rematerialize", "re-materialize", "re_materialize")
MIN_SSRF_SEAM_USES = 4
SEED_ROW = "pre_call_notify"
SEED_REGION_CHARS = 600
SEED_URL_KEY = '"url"'
SEED_HTTPS_PREFIX = '"https://'

SPEC_TAG = "spec 0046"
CONTRACT_REQUIRED_PHRASES = (
    SPEC_TAG,
    VERSION_ANCHOR,
    "409",
    DEPRECATION_ANCHOR,
    STALE_FLAG,
    SSRF_GATE,
    SEED_ROW,
    "propagation count",
)
API_REFERENCE_MARKER = "Shared-tool live-link (spec 0046)"
API_REFERENCE_REQUIRED_PHRASES = (
    API_REFERENCE_MARKER,
    "409",
    "400",
    "SSRF",
    STALE_FLAG,
    "propagation",
)

def _text(rel_path: str) -> str:
    """Read one repo file as text (grep pins never import peer modules).

    Args:
        rel_path: Repo-relative file path.

    Returns:
        The file's full text.
    """
    return (REPO_ROOT / rel_path).read_text(encoding=SOURCE_ENCODING)


def _block(text: str, marker: str) -> str:
    """Slice one method body out of a service file's text.

    Args:
        text: The file's full text.
        marker: The `async def` line opening the wanted method.

    Returns:
        The method's text, up to the next sibling definition.
    """
    tail = text[text.index(marker) + len(marker) :]
    ends = [pos for marker_end in BLOCK_END_MARKERS if (pos := tail.find(marker_end)) != -1]
    return tail[: min(ends)] if ends else tail


def test_ssrf_gate_identity_stable() -> None:
    """The attach-time SSRF seam keeps its name and gate (spec 0046 split home: service_tools)."""
    text = _text(AGENTS_SERVICE_TOOLS_PATH)
    assert SSRF_GATE in text
    assert SSRF_SEAM in text


def test_update_bumps_tools_version() -> None:
    """Slice A: `update_tool` stamps a new `tools_version` on edit (spec 0046 decision 1)."""
    assert VERSION_ANCHOR in _block(_text(TOOLS_SERVICE_PATH), UPDATE_TOOL_DEF)


def test_update_reports_propagation_count() -> None:
    """Slice A: `update_tool` re-materializes attached agents and returns the count."""
    text = _text(TOOLS_SERVICE_PATH)
    assert PROPAGATION_ANCHOR in _block(text, UPDATE_TOOL_DEF)
    assert TOOL_REFS_ANCHOR in text


def test_delete_refused_while_referenced() -> None:
    """Slice A: `delete_tool` answers 409 with the referencing count (no silent detach)."""
    text = _text(TOOLS_SERVICE_PATH)
    assert REFERENCE_ANCHOR in _block(text, DELETE_TOOL_DEF)
    assert any(anchor in text for anchor in CONFLICT_ANCHORS)


def test_deprecation_blocks_new_attaches() -> None:
    """Slice A: `deprecated` rows refuse NEW attaches (400 naming the tool, Slice A units pin the code)."""
    agents_text = _text(AGENTS_SERVICE_PATH)
    tools_text = _text(TOOLS_SERVICE_PATH)
    assert DEPRECATION_ANCHOR in agents_text or DEPRECATION_ANCHOR in tools_text


def test_agents_rematerialize_single_path() -> None:
    """Slice B: `_resolve_tool_refs` grows the idempotent re-materialize entry (no fork)."""
    text = _text(AGENTS_SERVICE_TOOLS_PATH)
    assert any(alias in text for alias in RE_MATERIALIZE_ALIASES)


def test_reads_flag_stale_materializations() -> None:
    """Slice B: reads surface `stale_deprecated` + version drift, never re-write on read."""
    text = _text(AGENTS_SERVICE_TOOLS_PATH)
    assert STALE_FLAG in text
    assert VERSION_ANCHOR in text


def test_ssrf_parity_embedded_path() -> None:
    """Slice C (landed): embedded urls pass the same gate as refs (def + ref + webhook + embedded)."""
    assert _text(AGENTS_SERVICE_TOOLS_PATH).count(SSRF_SEAM) >= MIN_SSRF_SEAM_USES


def test_seed_has_no_attachable_but_broken_rows() -> None:
    """Slice A seed decision (landed: deprecated-in-seed): the webhook row is either
    unseeded, carries a real endpoint, or is flagged deprecated (never attachable-broken)."""
    text = _text(TOOLS_SEED_PATH)
    if SEED_ROW not in text:
        return
    region = text[text.index(SEED_ROW) : text.index(SEED_ROW) + SEED_REGION_CHARS]
    if "deprecated" not in region:
        # The flag may trail the 600-char window; the whole entry still counts.
        entry_end = text.index("\n    ),", text.index(SEED_ROW))
        region = text[text.index(SEED_ROW) : entry_end]
    healthy = SEED_URL_KEY in region and SEED_HTTPS_PREFIX in region
    retired = "deprecated" in region
    assert healthy or retired, "seeded webhook row is neither endpointed nor deprecated"


def test_contract_carries_livelink_version_section() -> None:
    """Slice D docs: the tools CONTRACT appends the spec-0046 version section."""
    text = _text(TOOLS_CONTRACT_PATH)
    missing = [phrase for phrase in CONTRACT_REQUIRED_PHRASES if phrase not in text]
    assert missing == []


def test_api_reference_documents_livelink() -> None:
    """Slice D docs: API_REFERENCE carries the live-link behavior note."""
    text = _text(API_REFERENCE_PATH)
    missing = [phrase for phrase in API_REFERENCE_REQUIRED_PHRASES if phrase not in text]
    assert missing == []


def test_openapi_pointer_matches_code() -> None:
    """Slice D docs: openapi stays valid and points at the live-link contract owner."""
    text = _text(OPENAPI_PATH)
    assert "openapi:" in text
    assert "0046" in text


def test_docs_match_code_at_gate_time() -> None:
    """Integrator re-verify: every CONTRACT symbol resolves to a code anchor (retry after peers land)."""
    pairs = (
        (VERSION_ANCHOR, TOOLS_SERVICE_PATH),
        (TOOL_REFS_ANCHOR, TOOLS_SERVICE_PATH),
        (STALE_FLAG, AGENTS_SERVICE_TOOLS_PATH),
        (SSRF_SEAM, AGENTS_SERVICE_TOOLS_PATH),
        (DEPRECATION_ANCHOR, AGENTS_SERVICE_TOOLS_PATH),
    )
    failures = [f"{anchor} missing from {path}" for anchor, path in pairs if anchor not in _text(path)]
    assert failures == []
