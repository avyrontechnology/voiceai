"""Operator CLI: sync the provider catalog seed into the database (spec 0022).

Usage:
    .venv/bin/python voiceai/tooling/seed_catalog.py --dry-run
    .venv/bin/python voiceai/tooling/seed_catalog.py --live

`--dry-run` prints the per-row plan without writing (default, safe).
`--live` performs the version-aware sync (insert missing, replace stale
versions, leave current and grandfathered rows alone) and prints the result.
Reads configuration from the environment (same `.env` as the app).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import kept out of runtime
    from voiceai.modules.catalog.service import CatalogService

__all__ = ["main"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the seeder."""
    parser = argparse.ArgumentParser(description="Sync the provider catalog seed into the database.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print the plan without writing.")
    group.add_argument("--live", action="store_true", help="Perform the sync.")
    return parser.parse_args(argv)


async def _plan(service: CatalogService) -> tuple[list[str], list[str], list[str]]:
    """Split seed rows into (missing, stale, current) catalog ids without writing."""
    from voiceai.modules.catalog.seed import seed_entries

    stored = {row.catalog_id: row for row in await service.entries()}
    missing: list[str] = []
    stale: list[str] = []
    current: list[str] = []
    for entry in seed_entries():
        existing = stored.get(entry.catalog_id)
        if existing is None:
            missing.append(entry.catalog_id)
        elif existing.catalog_version < entry.catalog_version:
            stale.append(entry.catalog_id)
        else:
            current.append(entry.catalog_id)
    return missing, stale, current


async def _run(live: bool) -> int:
    """Execute the plan (or print it); return the process exit code."""
    from voiceai.core.container import build_container
    from voiceai.core.environment import load_environment

    environment = load_environment()
    print(f"target: backend={environment.db_backend} db={environment.db_name}")
    container = build_container(environment)
    service = container.catalog_service()
    try:
        if live:
            result = await service.ensure_seeded()
            print(f"done: {result['inserted']} inserted, {result['updated']} updated, {result['current']} current")
            return 0
        missing, stale, current = await _plan(service)
        print(f"plan: {len(missing)} to insert, {len(stale)} to update, {len(current)} current")
        for catalog_id in missing:
            print(f"  + {catalog_id}")
        for catalog_id in stale:
            print(f"  ~ {catalog_id}")
        return 0
    finally:
        from voiceai.core.container import aclose_container

        await aclose_container(container)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse, run, return the exit code."""
    args = _parse_args(argv)
    return asyncio.run(_run(live=args.live))


if __name__ == "__main__":
    sys.exit(main())
