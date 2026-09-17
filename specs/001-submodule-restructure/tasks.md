# Tasks: Submodule Architecture Restructure

**Input**: Design documents from `/specs/001-submodule-restructure/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED — spec FR-016 + Constitution VII mandate per-method suites; AGENTS.md mandates TDD (Red → verify fail → Green → Refactor). Every story phase writes tests FIRST.

**Organization**: Grouped by user story; each story is an independently testable increment. New code: full type hints + docstrings (purpose/args/returns/raises) on everything, DI-only dependencies, no `print()`/ad-hoc loggers.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Tooling, dependencies, and baselines before any code moves

- [X] T001 [P] Pin gate tools in `.pre-commit-config.yaml` (ruff v0.16.6 check+format, mypy 1.x, bandit 1.9.4 existing, import-linter)
- [X] T002 [P] Add `mongo` service to `docker-compose.yml` (mongo:8, voiceai-mongo, mongodata volume, mongosh ping healthcheck, app depends_on redis+mongo)
- [X] T003 [P] Add Beanie v2.x, Motor, import-linter, mypy to `requirements.txt`
- [X] T004 Run `pytest tests/ -q` and record the green baseline in `specs/001-submodule-restructure/baseline.md`
- [X] T005 [P] Create shim-removal registry `SHIMS.md` at repo root (old→new path + removal version columns, empty to start)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Automated enforcement that MUST exist before ANY story work

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T006 Create import-linter contracts in `.importlinter` (layers controllers>services>repositories; forbidden: controller→repository, service→direct-DB, `os.environ` outside `core/environment.py`, common→core, new imports of shim paths)
- [X] T007 Configure mypy in `pyproject.toml` (baseline `disallow_untyped_defs,warn_return_any,warn_unused_ignores,no_implicit_optional,python_version=3.10`; strict overrides for `voiceai.core`, `voiceai.common`, `voiceai.database`, `voiceai.otobaai_logger`)
- [X] T008 Add the 6-step gate job (`ruff check`, `ruff format --check`, `mypy`, `bandit`, `lint-imports`, `pytest`) to `.github/workflows/` mirroring pre-commit

**Checkpoint**: Foundation ready — `lint-imports` and `mypy` run clean on current code (contracts scoped to allow legacy paths until migrated); user story work can begin

---

## Phase 3: User Story 1 - Shared foundation available to all modules (Priority: P1) 🎯 MVP

**Goal**: `core/`, `common/`, `database/`, and the single logger exist, tested, and wired; any submodule can be scaffolded on them

**Independent Test**: From spec US1 — resolve a config value, logger, DB session factory, response envelope, pagination, and datetime helper from the foundation alone with no domain submodule; quickstart Scenario 1

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US1] Environment loader tests in `tests/test_core_environment.py` (single-reader rule, missing-key behavior, no `os.environ` elsewhere)
- [X] T010 [P] [US1] Container tests in `tests/test_core_container.py` (`provide_*()` resolution, per-seam `dependency_overrides` fakes, lifespan open/close)
- [X] T011 [P] [US1] Common-module tests in `tests/test_common_responses.py` (envelope fields ok/detail/error{code,message,error_id,retryable,component,details}; `page_size` clamped to "default 20, max 100"; out-of-range page returns empty page with correct total; datetimes tz-aware UTC)
- [X] T012 [P] [US1] Database base tests in `tests/test_database_base.py` (BaseDocument carries created_at/updated_at tz-aware UTC, created_by/updated_by, "is_active bool default True", "meta dict default {}"; every model Settings.name resolves to a `database/constants.py` entry; soft-delete True→False→restore audited)

### Implementation for User Story 1

- [X] T013 [P] [US1] Create `voiceai/core/environment.py` (SOLE `os.environ` reader; typed accessors; secrets never logged)
- [X] T014 [P] [US1] Create `voiceai/core/redis.py` (sole Redis factory from environment; pool owned by lifespan)
- [X] T015 [US1] Create `voiceai/core/db.py` (sole Motor client factory + `init_beanie()`; no globals; depends on T013)
- [X] T016 [US1] Create `voiceai/core/container.py` (`AppContainer` + typed `provide_*()` per dependency reading `request.app.state.container`; lifespan builds/closes Redis pool, Mongo client, TaskRegistry; depends on T013–T015)
- [X] T017 [P] [US1] Create `voiceai/common/responses.py` (success/error envelope builders per data-model.md Entity 3; `Internal error (ref <error_id>)` mapping)
- [X] T018 [P] [US1] Create `voiceai/common/pagination.py` (bounded page/page_size, total, empty-page semantics per data-model.md Entity 4)
- [X] T019 [P] [US1] Create `voiceai/common/datetime_utils.py` (UTC tz-aware now/parse/format helpers)
- [X] T020 [P] [US1] Create `voiceai/database/constants.py` (SCREAMING_SNAKE collection-name registry, one entry per collection in `platform/models.py`)
- [X] T021 [US1] Create `voiceai/database/base.py` (`BaseDocument(Document)` with the six audit fields, UTC stamping, active-only query mixin; depends on T020)
- [X] T022 [P] [US1] Create `voiceai/otobaai_logger/__init__.py` (thin wrapper over `helpers/logger_config.py`; DI-provided; redaction of api_key/api_token/authorization)
- [X] T023 [US1] Wire lifespan + container into the app factory in `voiceai/platform/router.py` (replace unclosed module-global Redis pool; keep `create_platform_app(store)` offline default; depends on T016)
- [X] T024 [US1] Full gate green on new packages in `voiceai/core/`, `voiceai/common/`, `voiceai/database/`, `voiceai/otobaai_logger/` + quickstart Scenario 1 pass (ruff, mypy strict, bandit, lint-imports, pytest)

**Checkpoint**: US1 independently functional — a new submodule can scaffold on the foundation; STOP and VALIDATE before the pilot (MVP!)

---

## Phase 4: User Story 2 - Pilot submodule in ten-file shape, zero behavior change (Priority: P2)

**Goal**: `voiceai/platform/` (17 files) migrated to models/repositories/controllers/services/errors/exceptions/utils/static_methods/helpers/constants with layer rules enforced and old paths shimmed

**Independent Test**: From spec US2 — pre/post test suites all green with identical observable behavior; reviewer finds no business logic in controllers, no DB access in services, no rules in repositories; quickstart Scenario 2

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T025 [P] [US2] Pilot service tests in `tests/test_platform_services.py` (every service method incl. AuthZ allow/deny and failure cases)
- [X] T026 [P] [US2] Pilot repository tests in `tests/test_platform_repositories.py` (CRUD/query per method across MemoryStore/Redis backends; neutral queries only)
- [X] T027 [P] [US2] Pilot route tests in `tests/test_platform_controllers.py` (every route incl. 422-validation and error-envelope cases per contracts H-01–H-03)

### Implementation for User Story 2

- [X] T028 [P] [US2] Create `voiceai/platform/models.py` (existing shapes inherit `BaseDocument`; `Settings.name` from registry; no field-semantics change)
- [X] T029 [P] [US2] Create `voiceai/platform/errors.py` + `voiceai/platform/exceptions.py` (module-namespaced stable codes, e.g. `platform.api_key_revoked`)
- [X] T030 [P] [US2] Create `voiceai/platform/constants.py` (module-level values; remove ad-hoc literals)
- [X] T031 [US2] Create `voiceai/platform/repositories.py` (`PlatformRepository` Protocol + Memory/Redis backends; collection names from registry only; depends on T028)
- [X] T032 [US2] Create `voiceai/platform/services.py` (business logic + service-layer AuthZ moved out of router/auth; constructor-injected repos; raises module errors only; depends on T029, T031)
- [X] T033 [US2] Create `voiceai/platform/controllers.py` (thin async routes: Pydantic-v2 validation → principal → one service call → envelope; no repo/DB imports; depends on T032)
- [X] T034 [P] [US2] Complete `voiceai/platform/utils.py`, `static_methods.py`, `helpers.py` (no business or DB logic; pure helpers only)
- [X] T035 [US2] Leave lazy PEP-562 shims at old `platform/*` paths (`__getattr__`/`__dir__` + DeprecationWarning with removal version; no routers/models in shims) and record entries in `SHIMS.md` (depends on T028–T034)
- [X] T036 [US2] Baseline-diff verification: full `pytest` matches T004 baseline, old/new imports resolve to identical objects, quickstart Scenario 2 pass, gate green

**Checkpoint**: US1 + US2 both work; the pilot is the reference example for all later migrations

---

## Phase 5: User Story 3 - Remaining submodules migrate one at a time (Priority: P3)

**Goal**: Every remaining subpackage in the ten-file shape with tests + shims, product shippable after each increment (one module per PR)

**Independent Test**: From spec US3 — per increment: full suite green, old paths work via shims, each business rule/query/error has exactly one home; suite-behavior identical to pre-increment

> Each module task follows the US2 pattern (tests-first → models/errors/constants → repositories → services → controllers → utils/static/helpers → shims + SHIMS.md entry → suite-green). Different modules are [P] across modules; steps within a module run in story order.

- [X] T037 [P] [US3] Migrate `voiceai/agent_manager/` (7 files) incl. per-method service/repo/route tests with failure cases
- [X] T038 [P] [US3] Migrate `voiceai/synthesizer/` (16 files; funnel the 8 `os.environ` reads into `core/environment.py`) incl. tests + shims
- [X] T039 [P] [US3] Migrate `voiceai/transcriber/` (15 files) incl. tests + shims
- [X] T040 [P] [US3] Migrate `voiceai/llms/` (11 files) incl. tests + shims
- [X] T041 [P] [US3] Migrate `voiceai/input_handlers/` (10 files, incl. `telephony_providers/`) incl. tests + shims
- [X] T042 [P] [US3] Migrate `voiceai/output_handlers/` (11 files, incl. `telephony_providers/`) incl. tests + shims
- [X] T043 [P] [US3] Migrate `voiceai/agent_types/` (9 files) incl. tests + shims
- [X] T044 [P] [US3] Migrate `voiceai/s2s/` (5 files) incl. tests + shims
- [X] T045 [P] [US3] Migrate `voiceai/lid/` (5 files) incl. tests + shims
- [X] T046 [P] [US3] Migrate `voiceai/memory/` (3 files, incl. `cache/`) incl. tests + shims
- [X] T047 [P] [US3] Shrink `voiceai/helpers/` (15 files → move env/logging/resilience-adjacent concerns to `core/`/`common/`; leave pure helpers; shim moved names)

**Checkpoint**: All user stories' structural work complete; every module independently conforming

---

## Phase 6: User Story 4 - Tests, gates, and security hold on every module (Priority: P4)

**Goal**: Automated layer enforcement + security baseline verified per module; violations fail before merge, not in review

**Independent Test**: From spec US4 — per claimed-complete module: tests + gate + security checklist all pass with no unrecorded waivers; a planted cross-layer leak is caught automatically

- [X] T048 [P] [US4] Create automated layer-direction tests in `tests/test_layering.py` (controllers→services→repositories only; no controller→repository, no service→direct-DB; registry-completeness: all models resolve to `database/constants.py`)
- [X] T049 [P] [US4] Add secret/PII scan to CI (no credentials outside environment config; no `os.environ` outside `core/environment.py`; no caller PII in log statements) in `.github/workflows/` + `.pre-commit-config.yaml`
- [X] T050 [US4] Per-module security review recorded in `specs/001-submodule-restructure/security-review.md` (input validated at controller boundary per H-02; AuthZ in services per L-02; carrier/stream-token behavior preserved; Mongo least-privilege principal; error bodies carry error_id only) (depends on T048–T049)
- [X] T051 [US4] Planted-violation drill per quickstart Scenario 3 (temporary controller→repository import, direct client construction, stray `os.environ`; each must fail its gate step; then revert) (depends on T048)
- [X] T052 [US4] Full gate green per quickstart Scenario 6 (all six steps) across the migrated `voiceai/` tree

**Checkpoint**: Quality is sustained by automation, not vigilance — ready for polish

---

## Phase 7: User Story 2 follow-up - Platform god-file splits + ports/facades (Priority: P2)

**Goal**: `voiceai/platform/services.py` (2309 lines), `store.py` (1733), `mongo_store.py` (1540) each split by concern into files ≤1500 lines with `protocols.py` ports + public facade; zero behavior change

**Independent Test**: Per-domain service tests + per-collection repository tests green; facade-only import test green; full suite matches baseline; quickstart Scenario 2 + 7 re-pass

### Tests for Phase 7 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T053 [P] [US2] Service-split tests in `tests/test_platform_services_split.py` (every per-domain service method incl. AuthZ allow/deny and failure cases preserved from `tests/test_platform_services.py`)
- [X] T054 [P] [US2] Repository-split tests in `tests/test_platform_repositories_split.py` (per-collection CRUD/query across Memory/Redis/Mongo backends; neutral queries only per L-03)
- [X] T055 [P] [US2] Facade tests in `tests/test_platform_facade.py` (only `voiceai.platform` facade importable from outside; `protocols.py` ports cover every service/repository seam per data-model.md Entity 8)

### Implementation for Phase 7

- [X] T056 [US2] Split `voiceai/platform/services.py` (2309 lines) into `voiceai/platform/services/` per-domain modules (agents, batches, executions, graphs) each "MUST be ≤ 1500" lines with constructor-injected repository protocols (depends on T053)
- [X] T057 [US2] Split `voiceai/platform/store.py` (1733 lines) + `voiceai/platform/mongo_store.py` (1540 lines) into `voiceai/platform/repositories/` per-backend modules (`base.py` Protocol, `memory.py`, `redis.py`, `mongo/` per-collection-group mixin subpackage) each "MUST be ≤ 1500" lines, collection names from `voiceai/database/constants.py` only (depends on T054)
- [X] T058 [P] [US2] Create `voiceai/platform/protocols.py` (`typing.Protocol` ports for repository/service seams) and export ONLY the public surface from `voiceai/platform/__init__.py` (depends on T055)
- [X] T059 [US2] Leave lazy PEP-562 shims at moved `voiceai/platform/*` paths with removal versions, record in `SHIMS.md`, verify suite green + quickstart Scenarios 2 and 7 pass (depends on T056–T058)

**Checkpoint**: Platform has no file over 1500 lines and a facade-only surface; US2 reference pattern complete

---

## Phase 8: User Story 3 extension - Voice consolidation + pipeline split (Priority: P3)

**Goal**: New `voiceai/voice/` parent owning `pipeline/stt/tts/llm/s2s/lid/io/agents/memory` per `contracts/voice-module-contracts.md` V-01–V-05; `task_manager.py` (9070) split into 7 concern files; product shippable after each increment (one subpackage per PR)

**Independent Test**: Per increment — full suite green, old paths work via shims with DeprecationWarning, `tests/test_file_budget.py` trends to zero violators; quickstart Scenario 8 per subpackage

### Tests for Phase 8 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T060 [P] [US3] Pipeline-split tests in `tests/test_voice_pipeline_lifecycle.py`, `tests/test_voice_pipeline_llm.py`, `tests/test_voice_pipeline_ingress.py`, `tests/test_voice_pipeline_egress.py`, `tests/test_voice_pipeline_langswitch.py` (each concern incl. failure/edge cases; facade re-exports identical `TaskManager`)
- [ ] T061 [P] [US3] File-budget test in `tests/test_file_budget.py` (every `voiceai/**/*.py` "MUST be ≤ 1500" lines per data-model.md Entity 7; tracked pre-split violators listed explicitly until split)
- [ ] T062 [P] [US3] Voice shim-identity tests in `tests/test_voice_shims.py` (old `voiceai.synthesizer.*`/`transcriber.*`/`llms.*`/`s2s.*`/`lid.*`/`input_handlers.*`/`output_handlers.*`/`agent_manager.*`/`agent_types.*` resolve to canonical `voiceai.voice.*` objects with DeprecationWarning)

### Implementation for Phase 8

- [ ] T063 [US3] Create `voiceai/voice/__init__.py` + `voiceai/voice/pipeline/` splitting `voiceai/agent_manager/task_manager.py` (9070 lines) into `lifecycle.py`, `llm_tasks.py`, `transcriber_ingress.py`, `synthesizer_egress.py`, `language_switch.py`, `transfer_hangup.py`, `events_loop.py` (each "MUST be ≤ 1500" lines) + thin `task_manager.py` facade composing the mixins (depends on T060)
- [ ] T064 [P] [US3] Move `voiceai/transcriber/` → `voiceai/voice/stt/` with `protocols.py` + facade in `voiceai/voice/stt/__init__.py` + lazy shims at old paths + `SHIMS.md` entries (depends on T062)
- [ ] T065 [P] [US3] Move `voiceai/synthesizer/` → `voiceai/voice/tts/` with `protocols.py` + facade in `voiceai/voice/tts/__init__.py` + lazy shims at old paths + `SHIMS.md` entries (depends on T062)
- [ ] T066 [P] [US3] Move `voiceai/llms/` → `voiceai/voice/llm/` with `protocols.py` + facade in `voiceai/voice/llm/__init__.py` + lazy shims + `SHIMS.md` entries (depends on T062)
- [ ] T067 [P] [US3] Move `voiceai/s2s/` → `voiceai/voice/s2s/` with `protocols.py` + facade in `voiceai/voice/s2s/__init__.py` + lazy shims + `SHIMS.md` entries (depends on T062)
- [ ] T068 [P] [US3] Move `voiceai/lid/` → `voiceai/voice/lid/` with `protocols.py` + facade in `voiceai/voice/lid/__init__.py` + lazy shims + `SHIMS.md` entries (depends on T062)
- [ ] T069 [P] [US3] Merge `voiceai/input_handlers/` + `voiceai/output_handlers/` → `voiceai/voice/io/` with `protocols.py` + facade in `voiceai/voice/io/__init__.py` + lazy shims + `SHIMS.md` entries (depends on T062)
- [ ] T070 [P] [US3] Merge `voiceai/agent_types/` + `voiceai/agent_manager/` (minus pipeline) → `voiceai/voice/agents/` splitting `voiceai/agent_types/graph_agent.py` (1712 lines) into `voiceai/voice/agents/graph/` (executor, planner, tools; each "MUST be ≤ 1500" lines) + lazy shims + `SHIMS.md` entries (depends on T062)
- [ ] T071 [P] [US3] Move `voiceai/memory/` → `voiceai/voice/memory/` with `protocols.py` + facade in `voiceai/voice/memory/__init__.py` + lazy shims + `SHIMS.md` entries (depends on T062)
- [ ] T072 [P] [US3] Shrink `voiceai/helpers/` moving voice-adjacent concerns into `voiceai/voice/*/helpers.py`, shim moved names in `voiceai/helpers/__init__.py`, record in `SHIMS.md`
- [ ] T073 [US3] Suite-green verification per increment in `voiceai/voice/` (full `pytest`, old/new import identity, no sideways `voice/<sub>` → `voice/<other>` internals imports, quickstart Scenario 8 pass) (depends on T063–T072)

**Checkpoint**: All voice code under one parent, every file ≤1500 lines, every subpackage facade-only; US3 complete

---

## Phase 9: User Story 4 extension - Enterprise hardening + gates (Priority: P4)

**Goal**: `contracts/enterprise-contracts.md` E-01–E-05 enforced by automation on every migrated module (resilience adoption, logger consolidation, correlation, versioned surface); violations fail before merge

**Independent Test**: `tests/test_enterprise_guards.py` + `lint-imports` green per module; each planted violation class fails its gate; quickstart Scenarios 7, 9, 10 pass

- [ ] T074 [P] [US4] Enterprise-guards test in `tests/test_enterprise_guards.py` (ban bare `asyncio.create_task` outside `voiceai/core/resilience.py`; ban `print(` in `voiceai/`; ban `getLogger` outside `voiceai/otobaai_logger/` + `voiceai/helpers/logger_config.py`; ban `os.environ` outside `voiceai/core/environment.py`; facade-completeness per data-model.md Entity 8)
- [ ] T075 [P] [US4] Remove `print(` from `voiceai/agent_types/graph_based_conversational_agent.py` and `voiceai/platform/stream_token.py` routing through the DI-provided otobaai-logger (E-03)
- [ ] T076 [P] [US4] Adopt `TaskRegistry`/`safe_task` in `voiceai/voice/stt/` replacing bare `asyncio.create_task` in every transcriber + `transcriber_pool.py` (E-02)
- [ ] T077 [P] [US4] Adopt `TaskRegistry`/`safe_task` in `voiceai/voice/tts/`, `voiceai/voice/s2s/`, and `voiceai/voice/agents/` (`s2s_mixin.py`, `synthesizer_pool.py`) replacing bare `asyncio.create_task` (E-02)
- [ ] T078 [US4] Cover `voiceai/voice/pipeline/` loops with `iteration_guard`/`supervise`, bound provider awaits with `with_timeout`, route side paths through `call_soft`, verify teardown via `TaskRegistry.cancel_all` (E-02; depends on T063)
- [ ] T079 [US4] Add correlation-id plumbing reusing `new_error_id` from `voiceai/helpers/exceptions.py` (logs + error envelope per data-model.md Entity 9) and `/v1` route-version scaffold with frozen envelope fields per E-05 in `voiceai/platform/controllers.py`
- [ ] T080 [US4] Extend `.importlinter` with V-01–V-05 + E-01–E-05 contracts and add the file-budget + enterprise-guards steps to `.github/workflows/gate.yml` mirroring pre-commit (depends on T061, T074)
- [ ] T081 [US4] Planted-violation drills per quickstart Scenarios 3, 7, 9 (temporary sideways voice import, 1501-line file, bare `create_task`, stray `print(`; each must fail its gate step; then revert) (depends on T080)
- [ ] T082 [US4] Run quickstart.md Scenarios 7–10 end-to-end and record results in `specs/001-submodule-restructure/validation.md` (depends on T073, T081)

**Checkpoint**: Enterprise posture sustained by automation — ready for final polish

---

## Final Phase: Polish & Cross-Cutting Concerns (extended)

**Purpose**: Convergence (shims must not fossilize) and end-to-end proof

- [X] T083 [P] Retire shims that hit removal milestones: delete shim files, close `SHIMS.md` entries, full suite green (repeat per milestone)
- [X] T084 [P] Trend `rg "from voiceai.<old>"` counts to zero and add the CI check banning new shim-path imports
- [X] T085 Run quickstart.md Scenarios 1–6 end-to-end and record results in `specs/001-submodule-restructure/validation.md`
- [X] T086 Final full gate + suite green; update constitution Sync Impact comment removal if still present; close out `baseline.md` comparison
- [ ] T087 [P] Retire `voiceai/voice/` shims (`synthesizer`, `transcriber`, `llms`, `s2s`, `lid`, `input_handlers`, `output_handlers`, `agent_manager`, `agent_types` old paths) at removal milestones: delete shims, close `SHIMS.md` entries, suite green in `specs/001-submodule-restructure/validation.md` (depends on T073)
- [ ] T088 Final enterprise gate + suite green (`ruff check`, `ruff format --check`, `mypy`, `bandit`, `lint-imports` incl. V/E contracts, `pytest` incl. `tests/test_file_budget.py` + `tests/test_enterprise_guards.py`); update `baseline.md` comparison (depends on T082, T087)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3, P1)**: Depends on Foundational — MVP; blocks US2/US3 (they build on the foundation)
- **US2 (Phase 4, P2)**: Depends on US1 — proves the pattern; should precede mass migration
- **US3 (Phase 5, P3)**: Depends on US1+US2 pattern; modules within can run in parallel
- **US4 (Phase 6, P4)**: Depends on Foundational tooling; per-module application runs alongside/after US2–US3
- **Phase 7 (US2 follow-up, P2)**: Depends on US1+US2 (T028–T036); platform splits land before voice moves that import platform services
- **Phase 8 (US3 voice, P3)**: Depends on Phase 7 pattern + T061/T062 tests-first; subpackage moves T064–T072 parallel across modules (coordinate only on shared `SHIMS.md` appends); T063 pipeline split first (biggest risk)
- **Phase 9 (US4 enterprise, P4)**: Depends on Phase 8 outputs for loop coverage (T078); guard tests T074 + cleanups T075–T077 parallel anytime after Foundational
- **Polish (Final)**: Depends on all desired stories complete (T082–T084 after T073/T081)

### User Story Dependencies

- **US1 (P1)**: After Foundational; no story dependencies — the MVP
- **US2 (P2)**: After US1 (needs foundation + DI surface); independently testable via baseline-diff
- **US3 (P3)**: After US1+US2 pattern; each module independently testable; parallelizable across modules
- **US4 (P4)**: After Foundational tooling; applies to US1–US3 outputs; independently verifiable per module

### Within Each User Story

- Tests written FIRST and verified FAIL before implementation (TDD Red)
- Models/errors/constants before repositories; repositories before services; services before controllers; shims last
- Gate green + suite green before the story checkpoint

### Parallel Opportunities

- Phase 1: T001, T002, T003, T005 in parallel (T004 needs the suite runnable — run anytime)
- Phase 2: T006–T008 touch different files — parallel
- US1 tests T009–T012 parallel; impl T013/T014/T017–T020/T022 parallel; T015→T016→T023→T024 sequential tail
- US2 tests T025–T027 parallel; impl T028–T030 + T034 parallel; then T031→T032→T033→T035→T036
- US3: T037–T047 all parallel across modules (different directories; coordinate only on shared `SHIMS.md` appends)
- US4: T048 + T049 parallel; T050–T052 after
- Phase 7: T053–T055 tests parallel; T056 + T057 parallel (different files); T058 parallel with T056/T057; T059 sequential tail
- Phase 8: T060–T062 tests parallel; T064–T072 parallel across subpackages (different directories; T063 pipeline split lands first); T073 sequential tail
- Phase 9: T074–T077 parallel (different files); T078 after T063; T079 parallel; T080 after T061+T074; T081 after T080; T082 tail

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests together (TDD Red):
Task: "Environment loader tests in tests/test_core_environment.py [US1]"
Task: "Container tests in tests/test_core_container.py [US1]"
Task: "Common-module tests in tests/test_common_responses.py [US1]"
Task: "Database base tests in tests/test_database_base.py [US1]"

# Launch independent US1 implementation files together:
Task: "Create voiceai/core/environment.py [US1]"
Task: "Create voiceai/core/redis.py [US1]"
Task: "Create voiceai/common/responses.py [US1]"
Task: "Create voiceai/common/pagination.py [US1]"
Task: "Create voiceai/common/datetime_utils.py [US1]"
Task: "Create voiceai/database/constants.py [US1]"
Task: "Create voiceai/otobaai_logger/__init__.py [US1]"
```

## Parallel Example: User Story 3 (module swarm)

```bash
# Each module is an independent workstream (own directory + tests + shims):
Task: "Migrate voiceai/agent_manager/ [US3]"
Task: "Migrate voiceai/synthesizer/ [US3]"
Task: "Migrate voiceai/transcriber/ [US3]"
# ...one agent per module; suite-green check per PR before the next lands
```

## Parallel Example: Phase 8 (voice swarm)

```bash
# Each voice subpackage is an independent workstream (own directory + tests + shims):
Task: "Move voiceai/transcriber/ to voiceai/voice/stt/ [US3]"
Task: "Move voiceai/synthesizer/ to voiceai/voice/tts/ [US3]"
Task: "Move voiceai/llms/ to voiceai/voice/llm/ [US3]"
Task: "Move voiceai/s2s/ to voiceai/voice/s2s/ [US3]"
# ...one agent per subpackage; T063 pipeline split lands first, T073 verifies all
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T005)
2. Complete Phase 2: Foundational (T006–T008) — blocks everything
3. Complete Phase 3: US1 (T009–T024)
4. **STOP and VALIDATE**: quickstart Scenario 1 + gate green on new packages
5. Demo: scaffold one throwaway submodule purely from the foundation, then discard it

### Incremental Delivery

1. Setup + Foundational → enforcement ready
2. US1 → foundation usable (MVP!)
3. US2 → pattern proven on `platform/` with zero behavior diff
4. US3 → module-by-module rollout, one PR each, suite green every time
5. US4 → automation + security proof per module
6. Phase 7 → platform god-files split (no file >1500 lines) + facades
7. Phase 8 → voice consolidation + pipeline split, one subpackage per PR
8. Phase 9 → enterprise gates green (resilience, logger, correlation, versioning)
9. Polish → shims retired (incl. voice paths), Scenarios 1–10 recorded

### Parallel Team Strategy

1. Team completes Setup + Foundational + US1 together (all later work depends on it)
2. Once US2 lands the reference pattern:
   - Agent/swarm A–D: US3 modules in parallel (one module per worker per PR)
   - Gatekeeper worker: US4 checks on each merged module + owns T048–T052
3. Polish converges shims so the strangler finishes instead of fossilizing

---

## Notes

- [P] tasks = different files, no dependencies — safe to parallelize
- [Story] label maps each story-phase task to its spec user story for traceability
- Data-model constraints quoted verbatim in tasks (e.g. "is_active bool default True", "page_size default 20, max 100", tz-aware UTC) — no implementation-time discretion
- Commit after each task or logical group; stop at any checkpoint to validate the story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
