"""Generated-docs smoke gate (spec 0019, M0).

`make docs` renders `OWNERSHIP.md` + `ARCHITECTURE.md` from the registry into
a scratch directory and asserts structure markers — never a diff, because the
real outputs stay gitignored and CI cannot diff ignored files. Content
correctness (right squads, budgets, debt owners) is pinned by the registry,
size, and tenancy tests, not here.
"""

from __future__ import annotations

from pathlib import Path

from voiceai.tooling.render_docs import render


def test_docs_render_with_expected_structure(tmp_path: Path) -> None:
    """Both files render into any directory carrying the structural markers."""
    render(tmp_path)
    ownership = (tmp_path / "OWNERSHIP.md").read_text(encoding="utf-8")
    architecture = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert ownership.startswith("<!-- GENERATED")
    assert "| Module | Squad | Slack | Runbook | Budget (lines) |" in ownership
    assert "squad-voice" in ownership
    assert architecture.startswith("<!-- GENERATED")
    assert "## Modules (registry)" in architecture
    assert "## Shared kernel (stable map)" in architecture
    assert "## Size snapshot (live)" in architecture
