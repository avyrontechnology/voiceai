# executions

Call records: one row per call, whatever dialled it. Migrated out of the legacy
`platform` bucket in spec 0080, where the family was a `MemoryStore` behind an
opaque row shape and the engine, the simulated runner, the voice module and the
batch runner each held their own copy of the write path.

Read [CONTRACT.md](CONTRACT.md) for the routes and invariants,
[RUNBOOK.md](RUNBOOK.md) for operations.

## Layout

| File | Role |
|---|---|
| `constants.py` | Every literal: routes, scopes, collection, id prefix |
| `models.py` | `Execution`, `ExecutionStats`, `LatencyStats` — `BaseFields` documents |
| `schemas.py` | Request/response bodies mirroring the legacy API exactly |
| `errors.py` | `ExecutionNotFoundError` |
| `exceptions.py` | Guard helpers that raise it |
| `repository.py` | Storage, the legacy-envelope read, and the placed-call mapping |
| `service.py` | Business logic: reads, aggregates, and the runner entry point |
| `runner.py` | Both call lifecycles: the simulated state machine and the engine writer |
| `static_methods.py` | Stats and latency aggregation (pure) |
| `helpers.py` | Small mapping helpers |
| `utils.py` | Process-state utilities |

## Why the runner came with it

The records alone would have been the easy half. The two writers of a call row —
the simulated state machine the workflow engine calls, and the engine-side hook
that logs a finished run — live next to the records they write, so they moved
with them. Splitting records from their writers is what left the voice module
mirroring into a second collection in the first place.

`runner.py` declares the narrow `ExecutionStore` port it writes through instead
of importing the legacy store, so the workflow engine (spec 0079) can supply the
same port without either module knowing about the other's storage.

## What this module does not do

It does not dial. A real trunk dial stays in the voice module's outbound bridge;
the simulated runner here never touches a carrier, it walks the same states
offline so workflow and batch flows can be exercised.