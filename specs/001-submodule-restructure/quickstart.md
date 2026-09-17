# Quickstart: Validating the Restructure

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15

This guide proves each increment works end-to-end. It contains no
implementation code — implementation belongs in `tasks.md` and the build
phase. References point to `data-model.md` and `contracts/` instead of
duplicating them.

## Prerequisites

- Python 3.10, project dependencies installed (`requirements.txt`),
  Redis available (existing `docker-compose.yml`); MongoDB only for
  `database/`-scoped scenarios (see Scenario 5).
- Baseline captured before migrating: `pytest tests/ -q` result saved for
  comparison after each increment.

## Scenario 1 — Foundation resolves every shared capability

- **Setup**: check out the foundation increment (`core/`, `common/`,
  `database/`, logger wrapper).
- **Run**: targeted tests for the foundation packages; import the response
  envelope, pagination helper, datetime util, base record, collection
  registry, and one injected logger + config value in a scratch check.
- **Expected**: each capability resolves from exactly one location; no
  competing helper exists; stricter-than-baseline type checks pass on the
  new packages.

## Scenario 2 — Pilot module migrates with zero behavior change

- **Setup**: migrate the pilot (`platform/`) behind lazy shims at old paths.
- **Run**: full suite `pytest tests/ -q`; import the module via both old
  and new paths and confirm identical objects; exercise one route per
  controller through the test client.
- **Expected**: suite result matches the saved baseline; old and new
  imports resolve to the same canonical objects with a deprecation warning
  on the old path; every route returns the envelope in
  `contracts/http-contract.md` (H-01–H-03).

## Scenario 3 — Layering violations fail automatically

- **Setup**: on a scratch branch, add a temporary controller→repository
  import, a service constructing a client directly, and an `os.environ`
  read outside `core/environment.py`.
- **Run**: the gate sequence from `research.md` R-04
  (`ruff`, `mypy`, `bandit`, `lint-imports`, `pytest`).
- **Expected**: each planted violation fails its gate step; reverting
  restores green. (Proves `contracts/layer-contracts.md` is enforced, not
  advisory.)

## Scenario 4 — Security baseline holds on a migrated module

- **Setup**: pick any completed module.
- **Run**: secret scan (no credentials outside environment config);
  submit invalid/unauthorized requests to its routes; inspect emitted logs.
- **Expected**: invalid input → envelope error before service logic;
  unauthorized calls rejected by service-layer checks; logs contain no
  secrets or caller PII; error responses carry an `error_id` matching
  server-side detail.

## Scenario 5 — Persistence cutover (when `database/` lands)

- **Setup**: `mongo` service from compose; seed via the registry names in
  `data-model.md` Entity 2.
- **Run**: repository tests against memory, Redis, and Mongo backends;
  index assertions (unique email/key-hash, TTL expiry, compound
  org/agent/started_at).
- **Expected**: identical repository behavior across backends; unique and
  TTL indexes verified against real Mongo; audit fields (`created_at`,
  `updated_at`, actor ids, `is_active`, `meta`) present on every record.

## Scenario 6 — Full gate green (per-increment exit bar)

- **Run** (mirrored in pre-commit and CI): `ruff check .`,
  `ruff format --check .`, `mypy voiceai`, `bandit -r voiceai`,
  `lint-imports`, `pytest tests/`.
- **Expected**: all six green before any increment merges or any shim
  reaches its removal milestone.

## Scenario 7 — File-size budget holds (1500-line cap)

- **Setup**: any increment touching `voiceai/` (especially
  `voice/pipeline/` splits and `platform/` service/store splits).
- **Run**: `pytest tests/test_file_budget.py -q` plus the CI line-count
  step (`find voiceai -name '*.py' -exec wc -l {} + | sort -rn`);
  confirm the five baseline violators (`task_manager.py` 9070,
  `services.py` 2309, `store.py` 1733, `graph_agent.py` 1712,
  `mongo_store.py` 1540) shrink monotonically and every new/split file
  reports ≤ 1500 lines.
- **Expected**: budget test green; no file exceeds 1500 lines except
  explicitly tracked pre-split violators, each with a recorded
  concern-split (research R-06, contract V-04). Plant a temporary
  1501-line file to prove the gate fails, then revert.

## Scenario 8 — Voice module consolidates without behavior change

- **Setup**: migrate one voice subpackage (e.g. `transcriber/` →
  `voice/stt/`) behind lazy shims at the old paths.
- **Run**: full suite `pytest tests/ -q` (must match `baseline.md`);
  import the moved symbols via both old (`voiceai.transcriber.*`) and
  new (`voiceai.voice.stt.*`) paths and confirm identical objects with
  a deprecation warning on the old path; run a voice smoke path
  (transcribe → LLM → synthesize loop or its unit doubles).
- **Expected**: suite matches baseline; old/new imports resolve to the
  same canonical objects; no sideways `voice/<sub>` → `voice/<other>`
  imports exist (`lint-imports` per contract V-02 green); route and
  provider behavior unchanged per `contracts/http-contract.md`.

## Scenario 9 — Enterprise guards hold on a migrated module

- **Setup**: pick any completed module (`platform/` or one `voice/<sub>/`).
- **Run**: `pytest tests/test_enterprise_guards.py -q` (bans: bare
  `asyncio.create_task`, `print(`, ad-hoc `getLogger`, `os.environ`
  outside `core/environment.py`; checks: facade-only imports,
  `protocols.py` coverage) + `lint-imports`; plant one violation of each
  class on a scratch branch to prove each fails its gate.
- **Expected**: green on the module, red on each planted violation;
  `grep -rn "asyncio.create_task" voiceai/<module>` empty (outside
  `core/resilience.py`); `grep -rn "print(" voiceai/<module>` empty.

## Scenario 10 — Resilience + correlation survive faults

- **Setup**: migrated module with a long-lived loop (e.g.
  `voice/pipeline/` output loop, `voice/stt/` receiver).
- **Run**: inject a failing iteration (bad packet) and a slow provider;
  exercise teardown (cancel + `TaskRegistry.cancel_all`).
- **Expected**: loop logs the failure with an `error_id`, backs off, and
  continues (no silent death); slow provider raises classified timeout
  via `with_timeout`; teardown cancels all owned tasks within budget;
  client sees the safe envelope/fallback, never raw exception text or PII.
