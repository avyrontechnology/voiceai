# executions — Runbook

Call records, their aggregates, and the two writers of a call row. Migrated out
of the legacy `platform` bucket in spec 0080.

## Endpoints

| Method | Path | Scope | What it does |
|---|---|---|---|
| GET | `/api/v1/executions` | `platform:read` | List this tenant's call records, newest first |
| GET | `/api/v1/executions/{id}` | `platform:read` | One call record |
| GET | `/api/v1/executions/stats` | `platform:read` | Counts by status |
| GET | `/api/v1/executions/latency/summary` | `platform:read` | Latency percentiles and daily buckets |
| POST | `/api/v1/calls/simulate` | `calls:write` | Run one simulated call inline (202) |

Reads answer enveloped: `{"ok": true, "data": {...}}`. If a client is reading a
bare `{"executions": [...]}` off these five routes, it predates the cutover.

## Reading

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/executions/stats"
```

`stats.total` counts one tenant only. A count that looks too high next to the
list route is the first thing to check: the aggregates read the same tenant
scope, so a wrong number here is a scope bug, not a display bug.

## Latency

`latency/summary` reports `p50`/`p95` over `e2e_ms` and breaks the same calls
down per stage (`transcriber_ms`, `llm_ms`, `synthesizer_ms`). Calls with no
latency recorded are counted but excluded from the percentiles, so `count` and
the sample size can differ.

## Simulated calls

`POST /calls/simulate` with `delay_scale: 0` runs the whole state machine inline
and returns the finished record. Anything else queues it and progresses in the
background, answering `202` immediately — poll `/executions/{id}` for the result.

This never dials a carrier. It exists so workflow and batch flows can be
exercised offline.

## When a dial does not show up here

A placed call that 404s on its own detail route means the write went somewhere
this module does not read. That was the state after spec 0078: the voice module
mirrored placed rows into the platform store while these routes read the
executions collection, so two writers, one collection, and no visible record.

Check that the voice module's `VoiceCallService` was built with an
`executions_service` (the container does this). If it is `None`, placement still
succeeds and the record is simply dropped with a warning — placement is
deliberately never failed by a logging path.

## Related

- `batches/{id}/executions` and `batches/{id}/retry-failed` are still platform
  routes. They read through the executions service so they see the same rows;
  they move to their own module with the rest of the batch family.
- The workflow engine (spec 0079) calls the simulated runner through the
  `ExecutionStore` port, not through the legacy store.