"""Module-shape enforcement for 100-engineer parallel delivery (spec 0010).

Three mechanical checks over ``voiceai/modules/*/``, without importing module code:

1. Every module ships the strict-template surface: the canonical file set
   (``models.py`` may be a ``models/`` package once a module owns more than three
   models — agents/auth precedent), plus colocated ``CONTRACT.md``/``README.md``/
   ``RUNBOOK.md``.
2. Canonical top-level files stay under a line budget (ratchet: current max is
   ``auth/service.py`` at ~691 lines; the gate is set just above it so no file
   may *grow*, while new modules target ≤ 300 and services split over time).
   Legacy subtrees (``asr/``, ``tts/``, ``io/``, ``s2s/``, ``session/``,
   ``brains/`` — verbatim strangler moves) are excluded and tracked by
   ``TODO(spec-0010)`` burn-down, not by this gate.
3. Hygiene: no ``print(`` and no ``os.getenv`` in shipped canonical files.
   Config flows env-file → ``Environment`` → container → constructors
   (AGENTS.md rule 4); only ``core/environment.py``, ``*/adapters/`` (bridges),
   legacy subtrees, and tests may touch the process environment.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = REPO_ROOT / "voiceai" / "modules"

REQUIRED_DOCS: tuple[str, ...] = ("CONTRACT.md", "README.md", "RUNBOOK.md")

#: Canonical 10-file set files (plus schemas/ports/metrics, which are sanctioned extras).
REQUIRED_FILES: tuple[str, ...] = (
    "constants.py",
    "errors.py",
    "exceptions.py",
    "repository.py",
    "service.py",
    "controller.py",
    "helpers.py",
    "utils.py",
    "static_methods.py",
)

#: Ratchet budget (lines) for canonical top-level files. New modules target ≤ 300;
#: the gate sits just above the current max (auth/service.py ~788 after the spec
#: 0020 M1b tenant-enforcement methods) so nothing grows without a spec citing it.
#: Lower it as services split. See spec 0010 burn-down.
MAX_CANONICAL_LINES: int = 800

#: Legacy-move subtrees excluded from budgets/hygiene (verbatim strangler bodies,
#: not new code). Debt tracked via TODO(spec-0010), never accreted to.
LEGACY_SUBTREES: frozenset[str] = frozenset({"asr", "tts", "io", "s2s", "session", "brains"})

#: Files allowed to read the process environment (bridges + tests + the one declarer).
ENV_OK_PARTS: frozenset[str] = frozenset({"adapters", "tests"})
ENV_OK_FILES: frozenset[str] = frozenset({"scaffold_module.py"})

VIOLATION_SEPARATOR = "\n"


def _module_dirs() -> list[Path]:
    """Return every feature-module directory (has an ``__init__.py``)."""
    return sorted(
        path
        for path in MODULES_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file() and "__pycache__" not in path.parts
    )


def _has_models(mod: Path) -> bool:
    """Accept ``models.py`` or a ``models/`` package (dir allowance, spec 0010)."""
    return (mod / "models.py").is_file() or (mod / "models" / "__init__.py").is_file()


def _in_legacy_subtree(path: Path, mod: Path) -> bool:
    """True when the file lives under a verbatim-move legacy subtree."""
    return any(part in LEGACY_SUBTREES for part in path.relative_to(mod).parts)


def _canonical_files(mod: Path) -> list[Path]:
    """Top-level canonical files (budgets/hygiene apply here)."""
    return sorted(mod / name for name in REQUIRED_FILES if (mod / name).is_file())


def test_modules_ship_docs_and_canonical_set() -> None:
    """Every module has CONTRACT/README/RUNBOOK + the canonical 10-file set."""
    violations: list[str] = []
    for mod in _module_dirs():
        for doc in REQUIRED_DOCS:
            if not (mod / doc).is_file():
                violations.append(f"{mod.name}: missing {doc}")
        for filename in REQUIRED_FILES:
            if not (mod / filename).is_file():
                violations.append(f"{mod.name}: missing {filename}")
        if not _has_models(mod):
            violations.append(f"{mod.name}: missing models.py or models/__init__.py")
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_canonical_files_stay_within_line_budget() -> None:
    """No canonical top-level file may grow past the ratchet budget."""
    violations: list[str] = []
    for mod in _module_dirs():
        for path in _canonical_files(mod):
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > MAX_CANONICAL_LINES:
                violations.append(f"{path.relative_to(REPO_ROOT)}: {lines} lines > {MAX_CANONICAL_LINES}")
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_canonical_files_use_no_print_or_process_env() -> None:
    """No print() / os.getenv in shipped canonical files (config flows via Environment)."""
    violations: list[str] = []
    for mod in _module_dirs():
        for path in _canonical_files(mod):
            text = path.read_text(encoding="utf-8")
            if "print(" in text:
                violations.append(f"{path.relative_to(REPO_ROOT)}: print( is banned (use otobaai logger)")
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "os.getenv" in line or "os.environ" in line:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: process env is banned "
                        "(declare in core/environment.py, inject via container)"
                    )
    assert not violations, VIOLATION_SEPARATOR.join(violations)
