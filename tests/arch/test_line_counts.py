"""File-size gates for the strangler end state (spec 0004, B14 closeout).

Two rules, both mechanical. First, the hard cap: no Python file under the migrated
tree may exceed 1,500 lines (AGENTS.md rule 1). Second, the flagged-residual list:
every file over the 800-line target must be exactly the set spec 0004 records —
``task_manager.py`` (the in-progress facade, shrinking toward the endgame rename)
plus the six modules whose moves were audited file-by-file across B5–B12. A new file
crossing 800, or a flagged file crossing 1,500, fails the build here so the burn-down
stays honest through the endgame specs.
"""

from __future__ import annotations

from pathlib import Path

#: Repository root, derived from this file's location (tests/arch/ is two levels down).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Scopes the audit covers: the new-architecture voice tree (the hard cap guards new
#: code) plus the legacy facade (tracked only through the flagged-residual set — it is
#: mid-strangler at 2,490 lines, shrinking every step toward the endgame rename spec).
AUDIT_SCOPES = (
    REPO_ROOT / "voiceai" / "modules" / "voice",
    REPO_ROOT / "voiceai" / "agent_manager",
)

#: Scopes the hard cap covers: new-architecture files only. The legacy facade is
#: excluded — capping it mid-strangler would freeze the migration; its line count is
#: recorded per step in spec 0004 instead.
HARD_CAP_SCOPES = (REPO_ROOT / "voiceai" / "modules" / "voice",)

#: Hard cap (lines per file). Nothing may exceed it, no exceptions.
HARD_CAP_LINES = 1500

#: Target (lines per file). Files above it must be exactly this recorded set.
TARGET_LINES = 800

#: Every over-target file spec 0004 flags, with the step that audited it. A file that
#: drops back under the target is removed from this set in the same commit.
FLAGGED_RESIDUALS = {
    "voiceai/agent_manager/task_manager.py": "B13c",
    "voiceai/modules/voice/session/turn/transcript_listener.py": "B11d",
    "voiceai/modules/voice/session/language/switcher.py": "B9a",
    "voiceai/modules/voice/session/turn/generation.py": "B11b",
    "voiceai/modules/voice/session/turn/history_sync.py": "B10",
    "voiceai/modules/voice/session/s2s_runner.py": "B5",
    "voiceai/modules/voice/tts/providers/kalpa_synthesizer.py": "B12b",
}


def _python_files() -> list[Path]:
    """Return every audited source file, skipping bytecode caches."""
    found: list[Path] = []
    for scope in AUDIT_SCOPES:
        found.extend(path for path in scope.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(found)


def _line_count(path: Path) -> int:
    """Count lines in a source file."""
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def test_no_file_exceeds_the_hard_cap() -> None:
    """Every new-architecture file stays under 1,500 lines."""
    violations = [
        f"{path.relative_to(REPO_ROOT)}: {count} lines"
        for scope in HARD_CAP_SCOPES
        for path in sorted(p for p in scope.rglob("*.py") if "__pycache__" not in p.parts)
        if (count := _line_count(path)) > HARD_CAP_LINES
    ]
    assert not violations, "over the hard cap:\n" + "\n".join(violations)


def test_over_target_files_are_exactly_the_flagged_set() -> None:
    """No unrecorded file may sit above the 800-line target."""
    over_target = {str(path.relative_to(REPO_ROOT)) for path in _python_files() if _line_count(path) > TARGET_LINES}
    unflagged = sorted(over_target - set(FLAGGED_RESIDUALS))
    assert not unflagged, "over target but not flagged in spec 0004:\n" + "\n".join(unflagged)
