"""No parallel implementations: retired concerns stay retired (spec 0019, M0).

When a migration step retires a concern, its entry lands here naming the step's
spec; the legacy path must then be a tagged shim (``# legacy-shim(spec-NNNN)``)
or absent entirely. A second live implementation anywhere — legacy or new — is
a gate failure (principle 01). The ledger starts empty: nothing is retired yet.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VOICEAI_ROOT = REPO_ROOT / "voiceai"

#: Retired concerns: dotted legacy path → spec that retired it. M2+ appends in
#: the same spec that performs the retirement — never in advance, never after.
RETIRED: dict[str, str] = {}


def _legacy_path(concern: str) -> Path:
    """Resolve a dotted legacy path (``package.module``) to a file or package."""
    return VOICEAI_ROOT.joinpath(*concern.split("."))


def _is_tagged_shim(path: Path, spec: str) -> bool:
    """A pure re-export file carrying the owning spec's shim tag on line one."""
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    return bool(lines) and lines[0].startswith(f"# legacy-shim({spec})")


def test_retired_concerns_are_shims_or_absent() -> None:
    """Every retired concern is a tagged shim, a shimmed package, or gone."""
    violations = []
    for concern, spec in RETIRED.items():
        path = _legacy_path(concern)
        if not path.exists():
            continue  # absent: fully retired
        if path.is_dir():
            inits = path / "__init__.py"
            if _is_tagged_shim(inits, spec):
                continue  # package retired to a shim facade
            violations.append(f"{concern}: package still live (retired by {spec})")
        elif _is_tagged_shim(path, spec):
            continue  # file retired to a shim re-export
        else:
            violations.append(
                f"{concern}: live implementation outside a tagged shim (retired by {spec})"
            )
    assert not violations, "\n".join(violations)
