"""Duplicated-block detector across modules (spec 0019, M0).

Slides a normalized 30-line window over Python sources and reports blocks that
appear in two or more top-level units (``common``, ``core``, ``database``,
``modules/<name>``). Intra-unit duplication (e.g. provider skeletons inside
``voice/asr``) belongs to that module's own split spec, not this gate.

``DUP_DEBT`` records known-duplicated block hashes with owning specs (verbatim
parity delegators, characterization fixtures). A refactor that moves a debt
block changes its hash and fails loudly — update the entry in the same spec.
New duplicated blocks fail: extract shared code instead.

Usage:
    .venv/bin/python voiceai/tooling/dup_blocks.py [--min-lines 30] [--report]
    make dup
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    REPO_ROOT / "voiceai" / "common",
    REPO_ROOT / "voiceai" / "core",
    REPO_ROOT / "voiceai" / "database",
    REPO_ROOT / "voiceai" / "modules",
)

#: Minimum normalized lines for a block to count.
MIN_LINES = 30

#: Known-duplicated block hashes → owning spec. Generated from today's tree at
#: build time (spec 0019); entries go stale (hash moves) when the block is
#: refactored — update or delete them in the same spec, never widen silently.
DUP_DEBT: dict[str, str] = {}


def _unit_of(path: Path) -> str:
    """Top-level ownership unit: ``common``/``core``/``database``/``modules/<name>``."""
    relative = path.relative_to(REPO_ROOT / "voiceai")
    if relative.parts[0] == "modules":
        return "/".join(relative.parts[:2])
    return relative.parts[0]


def _normalized_lines(path: Path) -> list[str]:
    """Source lines stripped of leading/trailing whitespace, blanks dropped."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _iter_files() -> list[Path]:
    """Every ``.py`` file under the scan roots, excluding caches."""
    files: list[Path] = []
    for root in SCAN_ROOTS:
        files.extend(
            path for path in root.rglob("*.py") if "__pycache__" not in path.parts
        )
    return sorted(files)


def find_duplicates(min_lines: int = MIN_LINES) -> dict[str, list[tuple[str, int]]]:
    """Map block hash → sorted ``(repo-relative path, start line)`` hits.

    Only blocks spanning at least two ownership units are returned; the start
    line is counted in normalized (blank-stripped) coordinates.
    """
    hits: dict[str, list[tuple[str, int]]] = {}
    for path in _iter_files():
        lines = _normalized_lines(path)
        unit = _unit_of(path)
        rel = path.relative_to(REPO_ROOT).as_posix()
        for start in range(len(lines) - min_lines + 1):
            digest = hashlib.sha1("\n".join(lines[start : start + min_lines]).encode()).hexdigest()
            hits.setdefault(digest, []).append((rel, start))
    cross_unit = {
        digest: sorted(locations)
        for digest, locations in hits.items()
        if len({_unit_of(REPO_ROOT / rel) for rel, _ in locations}) > 1
    }
    return cross_unit


def main(argv: list[str] | None = None) -> int:
    """Entry point: report cross-unit duplicated blocks; fail on undebted ones."""
    parser = argparse.ArgumentParser(description="Fail on unlisted cross-module duplicated blocks.")
    parser.add_argument("--min-lines", type=int, default=MIN_LINES)
    parser.add_argument("--report", action="store_true", help="print debt-file lines for every hit")
    args = parser.parse_args(argv)
    cross_unit = find_duplicates(args.min_lines)
    if args.report:
        for digest in sorted(cross_unit):
            locations = ", ".join(f"{rel}:{line}" for rel, line in cross_unit[digest])
            print(f'    "{digest}": "spec-XXXX",  # {locations}')
        return 0
    undebted = {digest: locs for digest, locs in cross_unit.items() if digest not in DUP_DEBT}
    if undebted:
        print("duplicated 30+ line blocks across modules (extract shared code or debt them):")
        for digest in sorted(undebted):
            locations = ", ".join(f"{rel}:{line}" for rel, line in undebted[digest])
            print(f"  {digest[:12]}… {locations}")
        return 1
    print(f"dup clean: {len(cross_unit)} debted block(s), 0 undebted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
