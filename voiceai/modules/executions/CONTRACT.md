# executions CONTRACT

Owner: `squad-voice` (`#squad-voice`).

## Routes (mounted under `/api/v1`)

- `GET /executions` — list this tenant's call records, newest first (scope `platform:read`).
- `GET /executions/{execution_id}` — one call record (scope `platform:read`).
- `GET /executions/stats` — counts by status for this tenant (scope `platform:read`).
- `GET /executions/latency/summary` — latency percentiles and daily buckets (scope `platform:read`).
- `POST /calls/simulate` — run one simulated call inline, 202 (scope `calls:write`).

Every response is built from `common.responses`, so reads answer as
`{"ok": true, "data": ...}`. That envelope is a change from the bare JSON the
platform bucket served; clients on these five routes move with it.

## Invariants

- **One writer.** Call records live here and only here. The voice module dials and
  hands the row to `ExecutionsService.record_placed`; it does not write its own
  copy (that mirror is what made a dial 404 on its own detail route).
- **One tenant.** Reads and aggregates run over `TenantScopedRepository`, so a
  cross-tenant count is the same class of bug as the numbers scoping seam
  (spec 0074) and is treated as one.
- **Upsert on write.** The runner writes every state transition, starting from a
  call with no stored row, so `ExecutionRepository.save` inserts on first sight
  and updates after. A plain update would fail the first write of every call.
- **Legacy rows stay readable.** `as_execution` unwraps the pre-0080
  `{payload: {...}}` envelope, so rows written before the cutover keep serving.

## The runner

`runner.py` owns both callers of the call lifecycle: the simulated-call state
machine (`run_simulated_call`, `progress_simulated_call`) and the engine-side
writer (`record_engine_execution`). It takes an `ExecutionStore` port rather than
the legacy `MemoryStore`, which is what lets the workflow engine (spec 0079)
supply the same port and import a module instead of reaching into `platform`.

The simulated runner does not dial a carrier. It walks the same states a real
call does so workflow and batch flows can be exercised offline.

## Statuses

`queued`, `ringing`, `in_progress`, `completed`, `failed`, `no-answer`, `busy`,
`canceled` — eight states, carried over unchanged from the platform bucket.