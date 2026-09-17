# Data Model: Submodule Architecture Restructure

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15

This model covers the new shared records and registries introduced by the
restructure. It does not change domain semantics: existing document shapes
in `voiceai/platform/models.py` keep their fields and gain the base audit
fields by inheritance.

## Entity 1: BaseDocument (database/base.py)

The inherited record shape for every stored collection/table. Implemented
as a Beanie `Document` subclass (see `research.md` R-02).

| Field | Type | Rules |
|---|---|---|
| `created_at` | `datetime` (UTC, tz-aware) | Set once at insert; never updated afterward |
| `updated_at` | `datetime` (UTC, tz-aware) | Refreshed on every mutation |
| `created_by` | `str \| None` (user/principal id) | `None` only for system-seeded records |
| `updated_by` | `str \| None` (user/principal id) | Mirrors the actor of the last mutation |
| `is_active` | `bool`, default `True` | Soft-delete flag; queries default to active-only |
| `meta` | `dict`, default `{}` | Free-form extension payload; no PII/PHI by policy |

- **Relationships**: parent of every collection model (Organization, User,
  ApiKey, Execution, Batch, WorkflowDoc, GraphDoc, KnowledgeBase, Tool,
  Webhook, SessionRecord, Invite, etc.).
- **Validation**: datetimes must be tz-aware UTC (rejected otherwise);
  `meta` values are size-capped; service layer stamps actor ids, never the
  caller-supplied payload.
- **State transitions**: `is_active True → False` (soft delete; no hard
  delete path except explicit purge with its own AuthZ); `False → True`
  (restore, audited like any mutation).

## Entity 2: Collection Registry (database/constants.py)

The single source of collection/table names.

| Field | Type | Rules |
|---|---|---|
| name key | `str` constant (e.g. `USERS`) | SCREAMING_SNAKE, one per collection |
| value | `str` (physical collection name) | Lowercase, stable; renames are migrations |

- **Relationships**: referenced by each model's `Settings.name` and by any
  code needing a collection name (migrations, seeds, admin jobs).
- **Validation**: every model `Settings.name` MUST resolve to a registry
  entry (enforced by an automated test that imports all models); no string
  literal collection name may appear elsewhere (import-lint/grep gate).

## Entity 3: Response Envelope (common/responses.py)

The one standard API response shape, aligned with the existing
`voiceai.responses.ErrorEnvelope` (`ok`, `detail`, `error{code, message,
error_id, retryable, component, details}`).

| Field | Type | Rules |
|---|---|---|
| `ok` | `bool` | `True` for success, `False` for errors |
| `detail` / data | payload | Success payload; shape defined per route |
| `error.code` | `str` | Machine-readable code from module `errors.py` |
| `error.message` | `str` | Human-readable; never raw exception text, never PII |
| `error.error_id` | `str` | Correlation id for log lookup |
| `error.retryable` | `bool` | Whether the caller may retry |
| `error.component` | `str` | Originating module/layer |
| `error.details` | `dict` | Structured extras; redacted |

- **Validation**: error codes come only from the raising module's
  `errors.py`; unexpected exceptions map to `Internal error (ref
  <error_id>)` with full details server-side only.

## Entity 4: Pagination (common/pagination.py)

Shared pagination behavior for all list routes.

| Field | Type | Rules |
|---|---|---|
| `page` / `cursor` | `int` / `str` | One strategy per route family; 1-based pages |
| `page_size` | `int` | Bounded (default 20, max 100) |
| `total` | `int` | Total matching count for page-based listing |
| `items` | `list` | Envelope-wrapped result page |

- **Validation**: `page_size` clamped to the max; out-of-range pages return
  an empty page (not an error) with correct `total`.

## Entity 5: Module Test Suite (per-module, tests/)

Not stored data, but a tracked deliverable per module: tests for every
service method, repository method, and controller route, including failure
and edge cases; plus the layer-direction test (controllers→services→
repositories only) and the registry-completeness test from Entity 2.

## Entity 6: Voice Module (voice/, new parent 2026-09-16)

The consolidation parent for all voice-pipeline code. Not a runtime
record — a package-ownership boundary.

| Subpackage | Source | Contents |
|---|---|---|
| `voice/pipeline/` | `agent_manager/task_manager.py` split | `lifecycle`, `llm_tasks`, `transcriber_ingress`, `synthesizer_egress`, `language_switch`, `transfer_hangup`, `events_loop` + facade |
| `voice/stt/` | `transcriber/` | Per-provider transcribers + pool (all currently <1500, kept) |
| `voice/tts/` | `synthesizer/` | Per-provider synthesizers + pool (all currently <1500, kept) |
| `voice/llm/` | `llms/` | Provider LLMs + tool-call accumulator |
| `voice/s2s/` | `s2s/` | Realtime/live session handlers |
| `voice/lid/` | `lid/` | Language-ID detectors |
| `voice/io/` | `input_handlers/` + `output_handlers/` | Socket/telephony ingress + egress |
| `voice/agents/` | `agent_types/` + `agent_manager/` (minus pipeline) | Agent planners, interruption, voicemail |
| `voice/memory/` | `memory/` | Caches and conversation stores |

- **Relationships**: depends on `core/`, `common/`, `database/` only;
  never on `platform/` internals and never sideways between voice
  subpackages except through service interfaces (contract V-02).
- **Validation**: old import paths (`voiceai.synthesizer.*`, etc.) resolve
  via lazy shims to the canonical `voice.*` objects until removal
  milestones; registry-completeness test covers the old→new map.

## Entity 7: File-Size Budget (cross-cutting gate)

| Field | Type | Rules |
|---|---|---|
| `path` | `str` (repo-relative `.py`) | Every file under `voiceai/` |
| `lines` | `int` (`wc -l`) | MUST be ≤ 1500; CI + `tests/test_file_budget.py` fail otherwise |
| `violators` (baseline 2026-09-16) | `list` | `task_manager.py` 9070, `services.py` 2309, `store.py` 1733, `graph_agent.py` 1712, `mongo_store.py` 1540 — each with a recorded concern-split (research R-06) |

- **Validation**: splits preserve the ten-file layer shape per subpackage;
  no split may introduce a controller→repository import or any other
  L-01–L-04 violation to "save lines".

## Entity 8: Module Port + Facade (enterprise seam, per module)

Not stored data — a code-ownership boundary applied to every migrated
module (`platform/`, each `voice/<sub>/`).

| Element | Location | Rules |
|---|---|---|
| `protocols.py` ports | per module | `typing.Protocol` definitions for the module's repository, service, and provider seams; services depend on repository protocols, controllers depend on service protocols, all injected via `core/container.py` |
| `__init__.py` facade | per module | ONLY public surface (service interfaces, error codes, DTO models); no internal file (`services`, `repositories`, provider impls) importable from outside — enforced by import-lint + facade-completeness test |

- **Validation**: outside code importing `voice.<sub>.services` (or any
  non-facade path) fails the gate; facade exports resolve to the
  canonical objects (shim-identity rule, R-03).

## Entity 9: Request Correlation + Audit (cross-cutting)

| Field | Type | Rules |
|---|---|---|
| `error_id` / correlation id | `str` (reuse `helpers/exceptions.new_error_id`) | Generated per request/loop-iteration failure; carried in logs and returned in the error envelope (data-model Entity 3) |
| audit stamps | `created_by`/`updated_by` on `BaseDocument` | Stamped by the service layer from the authenticated principal, never from caller payload (Entity 1) |
| tenancy | `org_id` | Every repository query is org-scoped by the service; cross-org access is an AuthZ failure, never an empty page |

- **Validation**: logs contain correlation id, never secrets or caller
  PII; error responses carry `Internal error (ref <error_id>)` for
  unexpected failures with full detail server-side only.

## Index summary (initial, extended per collection at migration)

- `users`: unique `email`
- `api_keys`: unique `key_hash`
- `sessions` / `invites`: unique `token_hash` + TTL on expiry
- `executions` / `batches`: compound `(org_id, agent_id, started_at)`
  (+ `status` where hot)
