# Implementation Plan: Submodule Architecture Restructure

**Branch**: `001-submodule-restructure` | **Date**: 2026-09-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-submodule-restructure/spec.md`
plus enterprise-hardening scope (2026-09-16): voice/ consolidation, 1500-line
file budget, ports/protocols per module, public facades, API versioning,
correlation/observability, resilience (TaskRegistry/iteration_guard/timeouts),
and uniform AuthZ/tenancy — all behavior-preserving.

## Summary

Reorganize the `voiceai/` package (~64k LOC, 184 files) into
submodule-based architecture: every submodule keeps exactly the ten
standard files (models, repositories, controllers, services, errors,
exceptions, utils, static_methods, helpers, constants) with strict layer
rules, on top of a new shared foundation (`core/`, `common/`,
`database/`, single `otobaai-logger` wrapper). Per 2026-09-16 scope
extension, all voice-pipeline code consolidates under a new
`voiceai/voice/` parent module (stt, tts, llm, s2s, lid, io,
agents, pipeline, memory subpackages), and NO file anywhere may exceed
1500 lines — the five current violators (`agent_manager/task_manager.py`
9070, `platform/services.py` 2309, `platform/store.py` 1733,
`agent_types/graph_agent.py` 1712, `platform/mongo_store.py` 1540) are
split by concern into <1500-line files. Approach from research:
hand-rolled FastAPI-`Depends` composition root (no DI library), Beanie v2
ODM on a new Mongo service, lazy PEP-562 import shims for incremental
strangler migration, and a pinned ruff+mypy+bandit+import-linter gate as
the SonarQube equivalent plus a line-count gate. Behavior-preserving: the
full existing suite stays green after every increment.

## Technical Context

**Language/Version**: Python 3.10 (pinned `python:3.10.13-slim`,
`requires-python>=3.10`, ruff `target-py310`)

**Primary Dependencies**: FastAPI 0.115.6, Pydantic v2 (>=2.9.0),
`redis==5.0.1`, `websockets`/`aiohttp`, `litellm`/`openai`,
`pytest`+`pytest-asyncio` (`asyncio_mode=auto`); NEW: Beanie v2.x
(+ Motor/PyMongo async), `import-linter`, pinned `ruff v0.16.6`,
`mypy 1.x`, existing `bandit 1.9.4`

**Storage**: Redis (existing, kept behind repository interface) + new
MongoDB 8 service (compose) via Beanie; `MemoryStore` retained as the
default unit-test fake

**Testing**: `pytest tests/` (existing ~170 files must stay green) plus new
per-module service/repository/route suites incl. failure/edge cases,
layer-direction tests, registry-completeness tests, `mongomock-motor` for
repository units, real Mongo in CI for index/CAS tests

**Target Platform**: Linux server (Docker Compose: app + redis + mongo)

**Project Type**: Async web-service / streaming voice platform
(ASR → LLM → TTS → telephony over websockets)

**Performance Goals**: No regression vs baseline per increment; provider
awaits stay timeout-bounded; no blocking calls on the event loop
(constitution + AGENTS.md resilience rules carry over unchanged)

**Constraints**: Externally visible behavior frozen (route paths, status
codes, envelope fields); one module per PR; shims removed only at recorded
milestones; `line-length = 120`; HARD file-size budget: no `.py` file may
exceed 1500 lines (enforced by CI gate `awk 'FNR>1500'` check + ruff;
measured 2026-09-16: 5 violators listed in Summary, all others <1500).
Enterprise non-negotiables (constitution I–IX, research R-07): no bare
`asyncio.create_task` (TaskRegistry/safe_task only), no `print()`/ad-hoc
`getLogger` (otobaai-logger via DI only), no `os.environ` outside
`core/environment.py` (verified 2026-09-16: sole reader), Pydantic-v2
validation at controller boundary, AuthZ in services, PII/secret redaction,
per-module `protocols.py` ports + `__init__.py` public facade, versioned
routes (`/v1`) with deprecation policy.

**Scale/Scope**: 184 files / 20 subpackages migrated incrementally after
the foundation; pilot module is `platform/` (17 files, already closest to
the target shape); first voice increment is `voice/pipeline/` split of
`task_manager.py` (9070 → ~7 files); full `voice/` consolidation moves
synthesizer→`voice/tts`, transcriber→`voice/stt`, llms→`voice/llm`,
s2s→`voice/s2s`, lid→`voice/lid`, input/output_handlers→`voice/io`,
agent_manager+agent_types→`voice/agents`+`voice/pipeline`, memory→`voice/memory`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | How the plan satisfies it | Status |
|---|---|---|
| I. Layered Module Isolation | Ten-file target tree; `contracts/layer-contracts.md` L-01–L-04 + `contracts/voice-module-contracts.md` V-01–V-05; `voice/` parent groups pipeline/stt/tts/llm/s2s/lid/io/agents/memory without cross-voice imports except via services; 1500-line splits are by concern (mixins), never by layer violation; import-linter `layers`+`forbidden` enforcement; quickstart Scenarios 3, 7 plant violations to prove the gate | PASS |
| II. Typing Discipline | mypy baseline repo-wide + strict overrides on new layout; `provide_*()` fully annotated; no untyped defs in new code | PASS |
| III. Documentation | Docstrings (purpose/args/returns/raises) required on every new class/method; review gate per increment | PASS |
| IV. Dependency Injection Only | `core/container.py` composition root, FastAPI `Depends` surface (research R-01); constructor injection; lifespan-owned resources; no direct construction | PASS |
| V. Shared Infrastructure Boundaries | `core/environment.py` sole env reader; `core/db.py`/`core/redis.py` sole factories; `common/` envelope/pagination/datetime; `BaseDocument` + `database/constants.py` registry (data-model.md Entities 1–2, 4) | PASS |
| VI. Single Logger | In-repo `otobaai-logger` wrapper over existing `logger_config.py`, DI-provided; `print()`/ad-hoc loggers banned by gate | PASS |
| VII. Test Coverage | Per-method service/repo/route suites + edge cases per module; baseline-green rule per increment; quickstart Scenarios 1, 2, 5, 6 | PASS |
| VIII. Static Analysis Gate | Pinned ruff+mypy+bandit+import-linter+pytest sequence (research R-04) PLUS file-budget gate `test_file_budget.py` / CI line-count check (research R-06), mirrored in pre-commit and CI; exit bar per increment | PASS |
| IX. Security Baseline | Secrets confined; Pydantic-v2 controller validation (H-02); AuthZ in services (L-02); no PII/exception text to clients or logs (H-01); least-privilege Mongo principal; quickstart Scenario 4 | PASS |

**Post-design re-check (Phase 1 complete, updated 2026-09-16 for voice/ +
1500-line + enterprise scope)**: no new violations introduced by
`research.md` R-05/R-06/R-07, `data-model.md` Entities 6–9,
`contracts/voice-module-contracts.md` V-01–V-05,
`contracts/enterprise-contracts.md` E-01–E-05, or `quickstart.md`
Scenarios 7–10 — all artifacts reference and reinforce the gates above.
Overall: **PASS, no waivers, no Complexity Tracking entries.**

## Project Structure

### Documentation (this feature)

```text
specs/001-submodule-restructure/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── layer-contracts.md
│   ├── http-contract.md
│   ├── voice-module-contracts.md  # NEW 2026-09-16: V-01–V-05
│   └── enterprise-contracts.md    # NEW 2026-09-16: E-01–E-05
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

Single project — the new layout grows inside the existing package
(strangler), so no new top-level project directories.

```text
voiceai/
├── core/                 # NEW: environment.py, container.py, db.py, redis.py
├── common/               # NEW: responses.py, pagination.py, datetime_utils.py
├── database/             # NEW: base.py (BaseDocument), constants.py (registry)
├── otobaai_logger/       # NEW: wrapper over helpers/logger_config.py
├── platform/             # PILOT migration → ten-file shape (+ lazy shims)
├── voice/                # NEW parent for voice-pipeline code (2026-09-16)
│   ├── pipeline/         # task_manager.py split by concern, each <1500 lines
│   ├── stt/              # from transcriber/
│   ├── tts/              # from synthesizer/
│   ├── llm/              # from llms/
│   ├── s2s/              # from s2s/
│   ├── lid/              # from lid/
│   ├── io/               # input_handlers/ + output_handlers/ merged
│   ├── agents/           # agent_types/ + agent_manager/ (minus pipeline)
│   └── memory/           # from memory/
└── helpers/              # shrinks as concerns move to core/common

tests/
├── test_<module>_*.py    # per-module service/repo/route suites
├── test_layering.py      # NEW: import-direction + registry-completeness
├── test_file_budget.py   # NEW: no .py file exceeds 1500 lines
└── (existing ~170 files, kept green throughout)
```

**Structure Decision**: in-place strangler inside `voiceai/` (no new
top-level project). New foundation packages first, then `platform/` pilot,
then `voice/` consolidation (pipeline split first, then stt/tts/llm/s2s/
lid/io/agents/memory moves) one subpackage per PR, with lazy PEP-562 shims
and a central shim-removal registry (research R-03, R-05, R-06).
Workstream order and agent assignments are defined at `/speckit.tasks` /
implement time.

## Complexity Tracking

> No constitution violations — table intentionally empty.
