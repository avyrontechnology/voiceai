# Research: Submodule Architecture Restructure

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15

All unknowns from Technical Context are resolved below. Each item follows
Decision / Rationale / Alternatives considered.

## R-01: Dependency-injection mechanism for `core/container.py`

- **Decision**: Pure FastAPI `Depends` wiring over an explicit hand-rolled
  `voiceai/core/container.py` composition root. No new DI library.
- **Rationale**:
  - Native to the existing code (`platform/router.py` `get_store()` reading
    `app.state`, `platform/auth.py` `get_store` / `get_principal` /
    `require_scope` chains) — zero new dependency, zero Python 3.10 risk,
    fully `asyncio`-native (`async def` + `yield` dependencies).
  - Strictest typing for Constitution II: plain fully-annotated
    `def provide_x() -> X` functions type-check under mypy with no
    plugin/stub gaps.
  - Best test/lifespan ergonomics: per-seam override via
    `app.dependency_overrides[provide_store] = Fake`; Redis/DB cleanup via
    standard `@asynccontextmanager lifespan` + `yield`-dependencies.
- **Alternatives considered**:
  - `dishka` — rejected: first-class async/scopes but adds a dependency,
    leaks `FromDishka` into signatures, no websocket auto-injection,
    overkill for a single-process app.
  - `dependency-injector` — rejected: mature but complex API, no per-request
    caching out of the box, singleton thread-safety and async-resource
    finalization pitfalls.
- **Integration notes**:
  - `container.py` is the sole factory: an `AppContainer` holding logger,
    redis client, store, services/repos, `TaskRegistry`; one typed
    `provide_*()` per dependency reading `request.app.state.container`.
  - Services take constructor args; they never construct stores/clients.
  - `lifespan()` builds the `redis.asyncio` pool (replacing the current
    unclosed module-global pool), yields, then disconnects and cancels the
    registry. Request-scoped DB sessions are `async yield` deps.

## R-02: MongoDB persistence (ODM) for `database/` + `core/db.py`

- **Decision**: Beanie ODM v2.x (on Motor/PyMongo async) for
  `voiceai/database/` models; raw Motor only inside the `core/db.py`
  client factory and migrations.
- **Rationale**:
  - Beanie 2.x supports Python `>=3.10` and is Pydantic-v2-native, matching
    the repo (`requires-python>=3.10`, `pydantic>=2.9`, FastAPI 0.115.6).
  - `class BaseDocument(Document)` inheritance directly implements the
    required `created_at/updated_at/created_by/updated_by/is_active/meta`
    audit fields plus per-collection `Settings(name, indexes)`, replacing
    hand-rolled codecs for the ~20 document types in
    `platform/models.py`.
  - Declarative indexes + `init_beanie()` replace the hand-maintained
    `platform:v1:` + `idx:...` Redis key scheme (SCAN/MGET, in-Python
    filtering, non-atomic wallet/CAS workarounds) with server-side
    indexes/transactions.
- **Alternatives considered**:
  - Raw Motor + hand-rolled models — rejected: duplicates
    validation/serialization/index/CAS code across ~20 collections with no
    inheritance.
  - Sync ODMs (MongoEngine-style) — rejected: blocking I/O violates the
    asyncio no-block rule; Pydantic-v1 legacy conflicts with the stack.
- **Integration notes**:
  - Compose: add `mongo` service (`mongo:8`, `voiceai-mongo`, `mongodata`
    volume, `mongosh ping` healthcheck); app `depends_on: [redis, mongo]`;
    `MONGO_URL`/`MONGO_DB` via `.env`; least-privilege app principal.
  - Base sketch: `BaseDocument(Document)` with the six audit fields,
    `Settings.name` drawn from `database/constants.py`; indexes e.g.
    executions `(org_id, agent_id, started_at)`, unique `users.email`,
    unique `api_keys.key_hash`, TTL on session/invite expiry.
  - Migration order: `constants.py` → `core/db.py` sole
    `get_client`/`init_beanie()` factory (no globals) → `BaseDocument` →
    stateless collections first (graphs/workflows/kbs/tools), then identity
    (users/api_keys/sessions/invites with unique-index backfill), then hot
    paths (executions/batches with compound indexes). `MemoryStore` /
    `RedisStore` stay behind a `PlatformRepository` Protocol with an
    env-selected backend for incremental cutover.
  - Tests: `MemoryStore` stays the default unit fake (zero infra, matches
    existing `tests/test_platform_*.py` + `asyncio_mode=auto`);
    `mongomock-motor` only for repository unit tests; real `mongo:8` in
    CI/compose for index/CAS/round-trip tests.

## R-03: Strangler-migration mechanics (import shims + milestones)

- **Decision**: Canonical-code-moved + thin lazy PEP-562
  (`__getattr__`/`__dir__`) shim left at the old path, emitting
  `DeprecationWarning` with a removal version. No logic or routers in shims.
- **Rationale**:
  - Lazy shims avoid the circular-import cycles that eager
    `from new import X` re-exports would worsen in 54k-LOC asyncio code,
    avoid double FastAPI router registration and duplicate class objects,
    and preserve pickle/`sys.modules` identity with one canonical
    definition.
  - Keeps the ~170-file suite green throughout: old import paths keep
    resolving while canonical code lives in exactly one place.
- **Alternatives considered**:
  - Eager re-export shims — rejected: execute the new module at old-import
    time, reintroduce cycles, duplicate routers/class objects.
  - `sys.modules`-swapper `ModuleType` subclasses — rejected: fragile,
    breaks static analysis/typing.
- **Integration notes**:
  - Shim skeleton: `__all__` + `__getattr__` (warn + delegate via
    `importlib.import_module(canonical)`) + `__dir__` + `sys.modules` alias
    preserving `__module__` for pickle; never define `APIRouter`/models in
    a shim.
  - Milestone tracking: central registry mapping old→new path + removal
    version; CI trends `rg "from voiceai.<old>"` counts to zero; an
    import-linter `forbidden` contract bans *new* imports of shim paths.

## R-04: Static-analysis gate stack (SonarQube equivalent)

- **Decision**: Pinned `ruff v0.16.6` + `mypy 1.x` (repo baseline, strict
  only on new code) + existing `bandit 1.9.4` + `import-linter` +
  `pytest` as the SonarQube-equivalent gate. No SonarQube server.
- **Rationale**:
  - Incrementally adoptable on current reality: minimal ruff config
    (`line-length 120`, py310), pre-commit has only bandit, no mypy/import
    checker exists — repo-wide `--strict` or a SonarQube server is
    unachievable now and would block the strangler.
  - `import-linter` `layers` + `forbidden` contracts give automated (not
    reviewer) layering enforcement mapping directly onto
    controllers→services→repositories.
  - All tools are pre-commit/CI-native and version-pinnable.
- **Alternatives considered**:
  - Full `mypy --strict` repo-wide — rejected: unpassable on legacy
    untyped asyncio/litellm code; blocks migration.
  - `tach` over import-linter — rejected: tag/`package.yml` sprawl and
    interactive setup overkill for a single-package monolith.
  - Standalone SonarQube server / ruff-`S`-only for secrets — rejected: no
    server known/maintainable here; bandit hook already pinned; PII/secrets
    need a gitleaks-style scanner supplement.
- **Integration notes** (gate sequence, mirrored in pre-commit + CI job):
  1. `ruff check . && ruff format --check .`
  2. `mypy voiceai` (baseline: `disallow_untyped_defs`,
     `warn_return_any`, `warn_unused_ignores`, `no_implicit_optional`,
     `python_version=3.10`; strict overrides on the new layout only)
  3. `bandit -r voiceai` (+ gitleaks-style secret scan supplement)
  4. `lint-imports` (layers controllers>services>repositories; forbidden:
     controller→repository, service→direct-DB, `os.environ` outside
     `core/environment.py`, common→core)
  5. `pytest tests/`
  - Move one module per PR with the full suite green before each
    shim-removal milestone.

## R-05: Voice-module consolidation (`voiceai/voice/` parent)

- **Decision**: New `voiceai/voice/` parent package owning eight
  subpackages: `pipeline/` (from `agent_manager/task_manager.py` split),
  `stt/` (from `transcriber/`), `tts/` (from `synthesizer/`), `llm/`
  (from `llms/`), `s2s/` (from `s2s/`), `lid/` (from `lid/`), `io/`
  (merged `input_handlers/` + `output_handlers/`), `agents/` (merged
  `agent_types/` + `agent_manager/` minus pipeline), `memory/` (from
  `memory/`). `platform/`, `core/`, `common/`, `database/`,
  `otobaai_logger/` stay top-level (shared foundation, not voice).
- **Rationale**:
  - Measured 2026-09-16: voice-pipeline code is spread over 10 top-level
    dirs (`agent_manager`, `agent_types`, `synthesizer`, `transcriber`,
    `llms`, `input_handlers`, `output_handlers`, `s2s`, `lid`, `memory`)
    with no single ownership point — exactly the discoverability failure
    SC-001 targets. One `voice/` parent gives "where does voice logic live"
    a single answer while preserving per-concern subpackages.
  - Keeps constitution Principle V intact: `core/` env/DI/redis/db and
    `common/` envelope/pagination/datetime remain the only cross-module
    providers; `voice/*` subpackages depend on foundation, never the
    reverse, and never on each other's internals (only via service
    interfaces) — enforced by import-linter `voice-module-contracts.md`
    V-01–V-05.
  - Incremental like the rest of the strangler: each move is
    canonical-code-moved + lazy PEP-562 shim at the old path (R-03),
    one subpackage per PR, suite green after each step.
- **Alternatives considered**:
  - Flat moves with no parent (keep 10 top-level dirs, just ten-file each)
    — rejected: preserves the current sprawl, fails the user's explicit
    "all voice related files in one module" request.
  - Single mega `voice.py` / merging providers into one file — rejected:
    would instantly violate the 1500-line budget and destroy provider-seam
    replaceability (constitution I rationale).
- **Integration notes**:
  - Move order: `pipeline/` split first (biggest risk, 9070 lines),
    then stateless moves (`stt/`, `tts/`, `llm/`, `s2s/`, `lid/`), then
    `io/`, `agents/`, `memory/`.
  - Old paths (`voiceai.synthesizer.*`, `voiceai.transcriber.*`, etc.)
    become lazy shims with `DeprecationWarning` + removal version,
    tracked in the central shim registry (R-03).

## R-06: 1500-line file budget and split strategy

- **Decision**: HARD budget — no `.py` file >1500 lines, enforced by
  `tests/test_file_budget.py` + CI line-count step (fails the gate like
  any ruff/mypy violation). Splits are by concern via mixins/facade, never
  by slicing a class arbitrarily.
- **Rationale** (grounded in 2026-09-16 measurement, `wc -l`, 184 files,
  64111 total):
  - Only 5 files violate the budget today, so the rule is adoptable
    incrementally: `agent_manager/task_manager.py` 9070 (single
    `TaskManager`, 177 members), `platform/services.py` 2309,
    `platform/store.py` 1733, `agent_types/graph_agent.py` 1712,
    `platform/mongo_store.py` 1540. All other files already pass,
    including the largest providers (`deepgram_transcriber.py` 1430,
    `helpers/utils.py` 1338 — near-limit, watched but not split yet).
  - `task_manager.py` method-size profile justifies concern-split:
    `__init__` 694, `run` 607, `_listen_transcriber` 455,
    `__run_language_switch` 467, `__do_llm_generation` 435,
    `__execute_function_call` 403, `_execute_transfer_call_webhook` 254,
    `sync_history` 225 — each cluster maps to one target file, each
    target stays well under 1500 lines (largest projected:
    `language_switch.py` ~1100, `llm_tasks.py` ~1200).
- **Alternatives considered**:
  - 2000-line budget — rejected: user explicitly set 1500; 1500 already
    passes for 179/184 files, so no leniency needed.
  - Arbitrary line-count slicing (split mid-class by line number) —
    rejected: breaks cohesion; concern-mixins preserve SC-001
    ("exactly one file per question").
  - SonarQube-only enforcement — rejected: no server provisioned (R-04);
    a 10-line pytest + `wc -l` CI step is immediate and deterministic.
- **Integration notes** (target splits):
  - `task_manager.py` 9070 → `voice/pipeline/`: `lifecycle.py`
    (init/setup/warm-pool ~1100), `llm_tasks.py` (generation/function-call
    ~1200), `transcriber_ingress.py` (listen/handlers ~900),
    `synthesizer_egress.py` (output loop/send ~800),
    `language_switch.py` (LID gate + switch ~1100),
    `transfer_hangup.py` (transfer webhooks/hangup ~700),
    `events_loop.py` (run/event dispatch ~800) + thin
    `task_manager.py` facade (~150, re-exports `TaskManager` composed
    from mixins for back-compat).
  - `platform/services.py` 2309 → `services/` split by domain
    (agents/batches/executions/graphs), `store.py` 1733 / `mongo_store.py`
    1540 → `repositories/` per-collection modules, `graph_agent.py` 1712
    → `agents/graph/` (executor + planner + tools).
  - Gate: `tests/test_file_budget.py` walks `voiceai/**/*.py`, asserts
    `len <= 1500`; CI runs `find voiceai -name '*.py' -exec wc -l` trend
    to zero violators before shim-removal milestones.

## R-07: Enterprise hardening (ports, facades, observability, resilience)

- **Decision**: Harden the ten-file + `voice/` layout to enterprise grade
  without changing runtime behavior: (a) per-module `protocols.py` ports
  (Repository/Service Provider Protocols) + `__init__.py` public facade —
  nothing outside a module imports its internals; (b) versioned HTTP
  surface (`/v1` prefix, envelope unchanged, deprecation via shims per
  R-03); (c) correlation-ID + structured logging via the single
  otobaai-logger through DI; (d) resilience uniformly applied —
  `TaskRegistry`/`safe_task` for every background task,
  `iteration_guard`/`supervise` for every long-lived loop,
  `with_timeout` on every provider await, `call_soft` for side paths;
  (e) AuthZ/tenancy uniformly in services (`require_scope` principal in,
  org-scoped repository queries, least-privilege principals).
- **Rationale** (measured 2026-09-16, grep over `voiceai/**/*.py`):
  - Bare `asyncio.create_task` is widespread (~20+ files: every
    transcriber, several synthesizers, `s2s_mixin.py:10`,
    `task_manager.py:43`) while `core/resilience.py` already ships the
    approved primitives (`TaskRegistry`, `safe_task`,
    `iteration_guard`, `supervise`, `with_timeout`, `call_soft`) and
    `core/container.py` already owns an app-level registry + lifespan
    teardown — so the fix is adoption, not invention.
  - Logger sprawl is small and closable: `print(` in 3 files
    (`graph_based_conversational_agent.py`, `stream_token.py`,
    `otobaai_logger/__init__.py`) and `getLogger` in 5 files
    (`responses.py`, `core/db.py`, `llms/litellm.py`,
    `otobaai_logger/__init__.py`, `helpers/logger_config.py`) — all
    routable to DI-provided otobaai-logger (constitution VI).
  - Config is already confined: `os.environ` reads exist ONLY in
    `voiceai/core/environment.py` — the enterprise rule is to keep it
    that way via the forbidden-import gate, not to re-plumb config.
  - AuthN/Z seams already exist (`platform/auth.py require_scope` chains,
    `carrier_auth`, `stream_token`) — enterprise work is uniform
    application (every route authenticates, every service authorizes,
    every repository query is org-scoped), enforced by per-module tests
    rather than new frameworks.
- **Alternatives considered**:
  - New DI/observability frameworks (dishka/OTel SDK wiring now) —
    rejected per R-01/R-04: adds deps and Python-3.10 risk for zero
    behavioral gain; hand-rolled container + logger wrapper +
    correlation-id middleware cover the need.
  - Big-bang clean-architecture rewrite (new `src/`, domain/app/infra
    split in one PR) — rejected: 64k LOC / 184 files cannot land safely
    at once; the strangler with per-PR moves + shims + green suite keeps
    the product shippable (spec FR-022, baseline rule).
  - Per-team shared-kernel library extraction — rejected: premature;
    `core/` + `common/` + `database/` already are the shared kernel.
- **Integration notes**:
  - Each migrated module ships `protocols.py` (typing.Protocol ports for
    its repository/service/provider seams, injected via
    `core/container.py`) and exports only its facade from
    `__init__.py`; import-linter forbids deep imports
    (`voice.<sub>.services` internals from outside).
  - Correlation ID: middleware/filter generates `error_id` per request
    (reuse `helpers/exceptions.new_error_id`), logger includes it, error
    envelope returns it; no PII/exception text to clients (H-01).
  - Gate additions: ban `asyncio.create_task` (except inside
    `core/resilience.py`), ban `print(`, ban `getLogger` outside
    `otobaai_logger/` + `helpers/logger_config.py`, ban `os.environ`
    outside `core/environment.py`; `tests/test_enterprise_guards.py`
    covers all four plus facade-completeness.

## Resolved unknowns

No `NEEDS CLARIFICATION` items remain. Prior user decisions (shared infra
first; MongoDB backend; in-repo logger wrapper) plus the assumed
incremental-strangler rollout (recorded in the spec, changeable at
`/speckit.clarify`) cover all Technical Context entries.
