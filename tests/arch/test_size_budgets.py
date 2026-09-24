"""Size budgets: hard file ceilings and module totals (spec 0019, M0).

Mechanical checks (no imports, pure line counts). The hard ceiling covers the
new-architecture roots plus tooling; the over-800 debt check spans the tree:

1. No file under the hard-ceiling roots exceeds 1,500 lines — no exemptions.
   Legacy trees shrink through their migration specs instead (principle 08).
2. Files over 800 lines anywhere fail unless listed in ``SIZE_DEBT`` with the
   owning spec id. Debt entries that shrink to 800 or below fail as stale
   (remove them) — budgets only ratchet.
3. Each registered module's total stays under its registry ``max_lines``;
   kernel dirs (``common``/``core``/``database``) stay under ``KERNEL_BUDGETS``.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VOICEAI_ROOT = REPO_ROOT / "voiceai"
MODULES_ROOT = VOICEAI_ROOT / "modules"

#: Roots the hard ceiling applies to: new architecture plus tooling. Legacy
#: trees (agent_manager, platform, transcribers, handlers, …) shrink through
#: their migration specs instead, so their over-800 files live in SIZE_DEBT
#: (principle 08: monotonic shrinkage, not day-one gates on legacy).
HARD_ROOTS = (
    VOICEAI_ROOT / "common",
    VOICEAI_ROOT / "core",
    VOICEAI_ROOT / "database",
    MODULES_ROOT,
    VOICEAI_ROOT / "tooling",
)

#: Hard ceiling: no file anywhere under voiceai/ may exceed this. No exemptions.
HARD_FILE_LINES = 1500
#: Target ceiling: files over this must name their owning split spec here.
DEBT_FILE_LINES = 800

#: Debt measured from today's tree (spec 0019): path → owning split spec.
#: M5 (0024): TaskManager decomposition + five turn-file splits.
#: M6 (0025): platform router/store retirement. M3 (0022): audio move + skeletons.
SIZE_DEBT: dict[str, str] = {
    "voiceai/agent_manager/task_manager.py": "spec-0024",
    "voiceai/modules/voice/session/turn/transcript_listener.py": "spec-0024",
    "voiceai/modules/voice/session/language/switcher.py": "spec-0024",
    "voiceai/modules/voice/session/turn/generation.py": "spec-0024",
    "voiceai/modules/voice/session/turn/history_sync.py": "spec-0024",
    "voiceai/modules/voice/session/s2s_runner.py": "spec-0024",
    "voiceai/platform/router.py": "spec-0025",
    "voiceai/platform/store.py": "spec-0025",
    "voiceai/helpers/utils.py": "spec-0022",
    "voiceai/modules/voice/tts/providers/kalpa_synthesizer.py": "spec-0022",
}

#: Kernel ceilings (blueprint target-tree budgets; measured well below today).
KERNEL_BUDGETS: dict[str, int] = {
    "common": 4000,
    "core": 4000,
    "database": 2500,
}


def _python_files() -> list[Path]:
    """Every ``.py`` file under ``voiceai/``, excluding bytecode caches."""
    return sorted(
        path
        for path in VOICEAI_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _hard_files() -> list[Path]:
    """Every ``.py`` file under the hard-ceiling roots (new arch + tooling)."""
    files: list[Path] = []
    for root in HARD_ROOTS:
        files.extend(
            path for path in root.rglob("*.py") if "__pycache__" not in path.parts
        )
    return sorted(files)


def _line_count(path: Path) -> int:
    """Count lines the way architects do: every line, including blanks."""
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def test_no_file_exceeds_hard_ceiling() -> None:
    """Files over 1,500 lines fail with no exemptions and no debt list."""
    violations = [
        f"{path.relative_to(REPO_ROOT)}: {count} lines (ceiling {HARD_FILE_LINES})"
        for path in _hard_files()
        if (count := _line_count(path)) > HARD_FILE_LINES
    ]
    assert not violations, "\n".join(violations)


def test_over_target_files_name_their_split_spec() -> None:
    """Files over 800 lines must be in SIZE_DEBT; shrunk entries must leave it."""
    counts = {path.relative_to(REPO_ROOT).as_posix(): _line_count(path) for path in _python_files()}
    over_target = {path for path, count in counts.items() if count > DEBT_FILE_LINES}
    unlisted = sorted(over_target - set(SIZE_DEBT))
    assert not unlisted, "over-800 files missing from SIZE_DEBT (add path → owning spec id):\n" + "\n".join(
        unlisted
    )
    stale = sorted(path for path in SIZE_DEBT if path not in over_target)
    assert not stale, "SIZE_DEBT entries at/below 800 lines (remove them — budgets only ratchet):\n" + "\n".join(
        stale
    )
    for path in sorted(over_target):
        assert SIZE_DEBT[path].startswith("spec-"), f"{path}: debt entry must name a spec id"


def test_module_totals_stay_within_registry_budgets() -> None:
    """Each module's total (code + colocated tests) respects its registry ceiling."""
    from voiceai.modules import ALL_MODULES

    totals: dict[str, int] = {}
    for path in _python_files():
        try:
            module = path.relative_to(MODULES_ROOT).parts[0]
        except ValueError:
            continue
        totals[module] = totals.get(module, 0) + _line_count(path)
    violations = [
        f"{module.name}: {totals.get(module.name, 0)} lines (budget {module.max_lines})"
        for module in ALL_MODULES
        if totals.get(module.name, 0) > module.max_lines
    ]
    assert not violations, "\n".join(violations)


def test_kernel_dirs_stay_within_budgets() -> None:
    """The shared kernel stays small: common/core/database have hard ceilings."""
    violations = []
    for dirname, budget in KERNEL_BUDGETS.items():
        total = sum(
            _line_count(path)
            for path in (VOICEAI_ROOT / dirname).rglob("*.py")
            if "__pycache__" not in path.parts
        )
        if total > budget:
            violations.append(f"voiceai/{dirname}: {total} lines (budget {budget})")
    assert not violations, "\n".join(violations)
