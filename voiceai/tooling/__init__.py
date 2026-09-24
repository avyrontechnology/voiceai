"""Developer tooling: module scaffolder and offline ops helpers (specs 0010/0011).

Moved here from `scripts/` because that directory is gitignored (spec 0018,
step zero) — tooling the gates depend on must be committable. Not imported by
runtime code; invoked directly (scaffolder) or by arch tests (backfill/seed
helpers).
"""

__all__: list[str] = []
