"""File size budget gate: no source file exceeds 800 lines (spec 0066).

Every .py file under the new-architecture roots must be at most 800 lines.
Existing violations are tracked in ``test_size_budgets.py`` SIZE_DEBT with
their owning split specs; this gate prevents new violations.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCH_ROOTS = [
    REPO_ROOT / "voiceai" / "common",
    REPO_ROOT / "voiceai" / "core",
    REPO_ROOT / "voiceai" / "database",
    REPO_ROOT / "voiceai" / "modules",
]

MAX_LINES = 800


def _source_files() -> list[Path]:
    """Return all Python source files under the architecture roots."""
    files: list[Path] = []
    for root in ARCH_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            files.append(path)
    return files


def test_no_file_exceeds_800_lines() -> None:
    """Every source file must be at most 800 lines (unless in SIZE_DEBT)."""
    debt = _load_size_debt()
    violations = []
    for path in _source_files():
        line_count = len(path.read_text().splitlines())
        if line_count > MAX_LINES:
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel not in debt:
                violations.append(f"{rel}: {line_count} lines (not in SIZE_DEBT)")
    assert not violations, (
        f"files exceeding {MAX_LINES}-line budget (split them or add to SIZE_DEBT):\n"
        + "\n".join(sorted(violations))
    )


def _load_size_debt() -> dict[str, str]:
    """Parse SIZE_DEBT from test_size_budgets.py without importing it."""
    path = REPO_ROOT / "tests" / "arch" / "test_size_budgets.py"
    text = path.read_text()
    debt: dict[str, str] = {}
    in_dict = False
    for line in text.splitlines():
        if line.startswith("SIZE_DEBT"):
            in_dict = True
            continue
        if in_dict:
            if line.startswith("}"):
                break
            if '":' in line:
                key = line.split('":')[0].strip().strip('"')
                spec = line.split('":')[1].strip().strip('",')
                debt[key] = spec
    return debt


def test_size_debt_entries_are_accurate() -> None:
    """Every SIZE_DEBT entry still exists and is over 800 lines."""
    stale: list[str] = []
    for path_str, _spec in _load_size_debt().items():
        path = REPO_ROOT / path_str
        if not path.exists():
            stale.append(f"{path_str} (missing — remove from SIZE_DEBT)")
            continue
        line_count = len(path.read_text().splitlines())
        if line_count <= MAX_LINES:
            stale.append(f"{path_str} ({line_count} lines — remove from SIZE_DEBT)")
    assert not stale, "SIZE_DEBT drift:\n" + "\n".join(sorted(stale))


def test_known_violations_have_split_specs() -> None:
    """Every file over 800 lines must be in SIZE_DEBT with an owning spec."""
    missing = []
    for path in _source_files():
        line_count = len(path.read_text().splitlines())
        if line_count > MAX_LINES:
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel not in _load_size_debt():
                missing.append(f"{rel} ({line_count} lines — add to SIZE_DEBT with split spec)")
    assert not missing, "unsplit violations:\n" + "\n".join(sorted(missing))
