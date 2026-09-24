"""Scaffold a new strict-template feature module (spec 0000).

Creates the canonical 10-file set plus CONTRACT/README/RUNBOOK and prints the
registry entry to paste into voiceai/modules/__init__.py. Never overwrites.

Usage:
    .venv/bin/python voiceai/tooling/scaffold_module.py billing --owner-squad squad-billing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULES_ROOT = REPO_ROOT / "voiceai" / "modules"

CANONICAL_FILES: tuple[str, ...] = (
    "constants.py",
    "models.py",
    "schemas.py",
    "errors.py",
    "exceptions.py",
    "ports.py",
    "repository.py",
    "service.py",
    "controller.py",
    "helpers.py",
    "utils.py",
    "static_methods.py",
    "metrics.py",
)

DOC_FILES: tuple[str, ...] = ("CONTRACT.md", "README.md", "RUNBOOK.md")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the scaffolder."""
    parser = argparse.ArgumentParser(description="Scaffold a strict-template module.")
    parser.add_argument("name", help="Module name, e.g. billing (lowercase, no spaces).")
    parser.add_argument("--owner-squad", required=True, help="Owning squad, e.g. squad-billing.")
    parser.add_argument("--slack", default="", help="Slack channel, defaults to #<owner-squad>.")
    return parser.parse_args(argv)


def _validate(name: str) -> str:
    """Validate the module name and return it normalized."""
    normalized = name.strip().lower()
    if not normalized.isidentifier():
        raise SystemExit(f"Invalid module name {name!r}: use a lowercase identifier.")
    if (MODULES_ROOT / normalized).exists():
        raise SystemExit(f"Refusing to overwrite existing module {normalized!r}.")
    return normalized


def _render_init(name: str) -> str:
    """Render the package __init__ with MODULE + narrow __all__."""
    return (
        f'""" {name} module (scaffolded by voiceai/tooling/scaffold_module.py)."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from voiceai.modules import ModuleDef\n"
        f"from voiceai.modules.{name}.constants import MODULE_NAME\n"
        f"from voiceai.modules.{name}.controller import router\n"
        "\n"
        "MODULE: ModuleDef = ModuleDef(\n"
        "    name=MODULE_NAME,\n"
        "    router=router,\n"
        f'    runbook_path="voiceai/modules/{name}/RUNBOOK.md",\n'
        "    max_lines=8000,  # tighten to the measured total before first merge (spec 0019)\n"
        ")\n"
        "\n"
        '__all__ = ["MODULE"]\n'
    )


def _render_constants(name: str) -> str:
    """Render the constants stub (every literal lives here or in env)."""
    upper = name.upper()
    return (
        '"""Every literal this module uses (AGENTS.md rule 1b)."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import Final\n"
        "\n"
        f'MODULE_NAME: Final[str] = "{name}"\n'
        f'ROUTE_PREFIX: Final[str] = "/{name}"\n'
        f'OWNER_SQUAD: Final[str] = "squad-{name}"\n'
        f"UPPER_{upper}: Final[str] = \"{upper}\"\n"
    )


def _scaffold(name: str, owner: str, slack: str) -> Path:
    """Create the module tree and return its root."""
    root = MODULES_ROOT / name
    root.mkdir(parents=True)
    (root / "adapters").mkdir()
    (root / "tests").mkdir()
    (root / "__init__.py").write_text(_render_init(name), encoding="utf-8")
    (root / "adapters" / "__init__.py").write_text('"""Legacy/driver bridges (only adapters may import them)."""\n', encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    for filename in CANONICAL_FILES:
        if filename in {"__init__.py", "constants.py"}:
            continue
        stem = filename[:-3].replace("_", " ")
        (root / filename).write_text(f'""" {name} {stem} (AGENTS.md rule 1)."""\n', encoding="utf-8")
    (root / "constants.py").write_text(_render_constants(name), encoding="utf-8")
    (root / "CONTRACT.md").write_text(
        f"# {name} CONTRACT\n\nOwner: `{owner}`.\n\n## Routes\n\n- `GET /api/v1/{name}` — TBD in the owning spec.\n\n"
        "## Events\n\n- in: TBD\n- out: TBD\n\n## Collections\n\n- TBD (`Collections` member, `tenant_id`-scoped when tenancy lands).\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(f"# {name}\n\nScaffolded module owned by `{owner}`. See `CONTRACT.md`.\n", encoding="utf-8")
    (root / "RUNBOOK.md").write_text(
        f"# {name} RUNBOOK\n\nOwner: `{owner}` ({slack}).\n\n## Alerts\n\nTBD.\n\n## Scaling\n\nTBD.\n\n## Rollback\n\nRevert the owning spec's merge; no migrations in scaffold.\n",
        encoding="utf-8",
    )
    return root


def main(argv: list[str] | None = None) -> int:
    """Entry point: validate, scaffold, print the registry paste-in."""
    args = _parse_args(argv)
    name = _validate(args.name)
    slack = args.slack or f"#{args.owner_squad}"
    root = _scaffold(name, args.owner_squad, slack)
    print(f"Created {root.relative_to(REPO_ROOT)} owned by {args.owner_squad} ({slack})")
    print("Paste into voiceai/modules/__init__.py:")
    print(f"  from voiceai.modules import {name}  # noqa: E402")
    print(f"  ... {name}.MODULE, ...  # with owner_squad={args.owner_squad!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
