"""Validate commit messages: conventional format + spec-id reference (AGENTS.md §7).

Reads the commit message from the file passed as argv[1] by the pre-commit
hook. Exits 0 when valid, 1 with a diagnostic otherwise.
"""

from __future__ import annotations

import re
import sys

CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|chore|docs|refactor|test|build|ci|perf|style|revert)"
    r"(\([a-z0-9_-]+\))?(!)?: .+"
)
SPEC_RE = re.compile(r"\[spec-\d{4}\]|spec-\d{4}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_commit_msg.py <commit-msg-file>")
        return 1

    with open(sys.argv[1], encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if not ln.startswith("#")]
    subject = lines[0] if lines else ""

    if not CONVENTIONAL_RE.match(subject):
        print("commit subject must follow conventional commits:")
        print("  type(scope): subject  (e.g. feat(tools): add oauth [spec-0061])")
        print(f"got: {subject!r}")
        return 1

    if not SPEC_RE.search(subject):
        print("commit subject must reference a spec: [spec-NNNN] or spec-NNNN")
        print(f"got: {subject!r}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
