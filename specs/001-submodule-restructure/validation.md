# Validation Record: quickstart Scenarios 1–6

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15 | **Task**: T055

Each scenario from `quickstart.md` with its observed outcome. All runs on
system python3.13 (full deps) unless noted.

## Scenario 1 — Foundation resolves every shared capability — PASS (US1)

- 42 foundation tests green (`test_core_*`, `test_common_foundation`,
  `test_database_base`): config value, logger, DB factories, envelope,
  pagination (`page_size` default 20 / max 100), datetime (tz-aware UTC),
  base record, registry — each from exactly one location.
- mypy strict on `core/common/database/otobaai_logger`, ruff clean.

## Scenario 2 — Pilot migrates with zero behavior change — PASS (US2)

- Full suite matches `baseline.md` (1990 passed + pre-existing
  `talko_dialer` failure, unchanged).
- Old/new imports resolve to identical objects (`is` check) with
  `DeprecationWarning(v0.11.0)` on the old path.
- Route table: 100 routes (86 moved + 14 auth), paths unchanged; smoke
  test (create 201, missing 404 with `ok:false` envelope) green.

## Scenario 3 — Layering violations fail automatically — PASS (US4/T051)

Drill executed for real, all reverted afterwards:
1. Planted `controllers → repositories` import → new
   `platform-controllers-never-touch-repositories` contract BROKEN
   (this drill also exposed and fixed two config bugs: wrong option
   name `ignore_type_checking_imports`, and missing
   `allow_indirect_imports`; layers contract alone permits the skip).
2. Planted `os.getenv` in `platform/helpers.py` → env gate CAUGHT.
3. Planted `int/str` assignment in `core/environment.py` → mypy CAUGHT.
- Post-drill: 5/5 contracts KEPT, ruff clean, no residue (`grep DRILL`
  empty).

## Scenario 4 — Security baseline holds — PASS (US4/T050)

- See `security-review.md`: secrets confined, boundary validation intact,
  service-layer AuthZ (11 org-check sites, tested), no PII in logs
  (grep-verified), carrier trust byte-identical, secret scan clean.

## Scenario 5 — Persistence cutover — DEFERRED (by design)

- `database/` + `core/db.py` + registry landed and tested (BaseDocument,
  Beanie wiring, index plan in `data-model.md`); no collection migrates
  in this epic (behavior freeze). Cutover is the defined follow-up with
  `MemoryStore`/`RedisStore` behind the `PlatformRepository` Protocol.

## Scenario 6 — Full gate green — PASS (US4/T052)

- `ruff check .` clean; `ruff format --check .` clean (400 files);
  `mypy` strict on 27 new-layout files clean; `bandit` 0 medium+
  repo-wide; `lint-imports` 5/5 kept; env gate clean (allowlist EMPTY);
  secret scan clean; shim-import census 0; full suite 2101+ passed with
  only the pre-existing `talko_dialer` failure.
