# Spec 0080: executions module (retire the executions / simulate families)

## Goal

Move the executions family — call records, their stats and latency readouts, and
the simulated-call runner — into a new module, `voiceai/modules/executions/`.

This is a **prerequisite spec**, not the last one. It moves first so the workflow
engine (spec 0081) can call the executions runner instead of
`platform.simulation.run_simulated_call`. Doing it in the other order would leave
the workflow engine holding a seam into legacy — the shape spec 0078 explicitly
rejected for graphs.

## Why it goes first

`run_workflow`'s `agent` node calls `run_simulated_call`, which belongs to this
family. Migrating workflows first would either:

- leave `run_simulated_call` imported from `platform` inside the new engine
  (a seam that spec 0078 says is a data-split waiting to happen), or
- force a half-migrated port with an unimplemented half.

This family is small and self-contained, so moving it costs one spec and removes
the seam entirely.

## Scope

### Records

`Execution` becomes a `BaseFields` document:

- `agent_id`, `batch_id | None`, `direction` (`outbound`|`inbound`),
  `to_number`, `from_number | None`, `status` (enum), `variables`, `transcript`,
  `summary`, `extracted_data`, `latency`, `hangup_code`, `started_at`,
  `ended_at`, `duration_s`.

Supporting value objects move with it: `TranscriptTurn`, `LatencyBreakdown`,
`ExecutionStatus` (a **9-value enum**, moved verbatim), `ExecutionStats`,
`LatencyStats`, `LatencyBucket`.

### Engine (`platform/simulation.py`)

`run_simulated_call` moves to the module. It is the function the workflow engine
calls, and it persists every state transition.

`simulate_router` (`POST /calls/simulate`) moves with it — it is the HTTP face
of the same runner.

### Routes

| Route | Notes |
|---|---|
| `GET /executions` | list, tenant-scoped |
| `GET /executions/stats` | aggregate counts |
| `GET /executions/{id}` | one |
| `GET /executions/latency/summary` | latency readout |
| `POST /calls/simulate` | run one simulated call |

Same paths, same scopes, same status codes. No route added or removed.

## Behaviours that must survive

- **Status is an enum** with nine states, not a free string. The state
  transitions in `run_simulated_call` (`QUEUED → RINGING → IN_PROGRESS →
  COMPLETED`/`FAILED`) are persisted at each step.
- **Stats and latency aggregates** are computed from the tenant's own executions
  only — a cross-tenant count is the same class of bug as the numbers scoping
  seam (spec 0074).
- **`direction`** (`outbound`/`inbound`) is preserved; workflow runs read
  `execution.status` to decide success.

## Data model

Collection `platform_executions` kept as-is. Renaming would orphan live call
records, which are the audit trail of who called whom and when.

## Security notes

- `to_number` / `from_number` are **PII** (phone numbers), and `variables` /
  `extracted_data` can hold customer content. Logged by execution id only, never
  dumped (AGENTS.md §4).
- Scopes unchanged: `platform:read` on the read routes.
- `run_simulated_call` is a **simulated** call — it dials nothing. It is moved,
  not changed; this spec does not alter whether it touches a carrier.
- Transcript turns carry call content: not logged.

## Architecture delta

```
NEW  voiceai/modules/executions/{constants,models,errors,repository,service,
                                 controller,schemas,runner,static_methods,
                                 exceptions,helpers,utils}.py
     + CONTRACT.md / README.md / RUNBOOK.md
MOD  voiceai/core/container.py   — executions provider + container.wire
MOD  voiceai/modules/__init__.py — executions registered
MOD  voiceai/platform/router.py  — executions_router + simulate_router removed
DEL  voiceai/platform/store.py   — execution methods
DEL  voiceai/platform/models.py  — Execution family models
DEL  voiceai/platform/simulation.py — run_simulated_call moves to the module
```

`platform/simulation.py` also carries other callers (`run_simulated_call` is used
by the legacy router today); only its workflow/router users move here.

## Test plan

- status enum has exactly its nine values and the transitions persist.
- stats and latency aggregates count this tenant's executions only.
- **tenant isolation** on the aggregate routes — a neighbouring tenant's volume
  must not appear.
- `run_simulated_call` runs against the in-memory driver and persists every
  transition (queued → ringing → in-progress → completed).
- the runner is callable from the workflow engine shape (returns an `Execution`
  with a `.status` the workflow can branch on).
- soft delete, legacy-envelope read.
- controller: all five routes through the real app factory with `httpx`, plus the
  scope gate.
- route-count pin.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

One commit, rollback is reverting it. Lower risk than the workflow family — the
runner is simulated and does not dial a carrier — but it is the piece the
workflow engine will depend on, so it is pinned by parity tests.

## Deviations

- This was to be a later spec; it moved ahead of workflows so the workflow engine
  has no legacy import. Recorded here because the ordering is the point of the
  spec.