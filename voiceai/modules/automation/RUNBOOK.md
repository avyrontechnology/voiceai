# Automation — Runbook

Graphs: records, snapshot history, and the graph engine. Migrated out of the
legacy `platform` bucket in spec 0078.

## Endpoints

| Method | Path | Scope |
|---|---|---|
| POST | `/api/v1/graphs` | `platform:write` |
| GET | `/api/v1/graphs` | `platform:read` |
| GET | `/api/v1/graphs/{id}` | `platform:read` |
| PUT | `/api/v1/graphs/{id}` | `platform:write` |
| DELETE | `/api/v1/graphs/{id}` | `platform:write` |
| GET | `/api/v1/graphs/{id}/versions` | `platform:read` |
| POST | `/api/v1/graphs/{id}/restore/{n}` | `platform:write` |
| POST | `/api/v1/graphs/{id}/validate` | `platform:write` |
| POST | `/api/v1/graphs/{id}/dry-run` | `platform:write` |
| POST | `/api/v1/graphs/{id}/deploy` | **`agents:write`** |

`deploy` is the outlier — it takes `agents:write`, unchanged from before.

## The snapshot history

Every graph has an ordered history, and it is append-only:

- `GET /graphs/{id}/versions` lists snapshots oldest-first.
- Restoring `POST /graphs/{id}/restore/{n}` **first snapshots the live state**
  (noted `before restore of v{n}`), then puts version *n*'s name and definition
  back.

So a restore is reversible: undoing it is restoring the version the restore
created. **Do not renumber versions** — a restore URL addresses a snapshot by its
number, and renumbering silently repoints every existing link at a different
snapshot.

## The engine routes

| Route | What it does |
|---|---|
| `/validate` | Engine rules + structure. `errors` block a deploy; `warnings` do not. |
| `/dry-run` | Deterministic structural walk — path, transcript preview, loop detection. Not LLM routing. |
| `/deploy` | Builds the `POST /agent` body that runs this graph on the engine. |

Validation mirrors the engine's hard rules, so a graph that would break at call
time fails validation first:

- routers never speak (a prompt or static message on one is an error)
- a router needs an unconditional catch-all edge
- a router may not use event edges
- unreachable nodes and empty prompts are warnings, not errors

## Troubleshooting

| Symptom | Check |
|---|---|
| 422 on validate/dry-run/deploy | The stored definition no longer validates. The detail carries locations and error *types* only. |
| A node unreachable warning | Unreachable nodes are a warning; the graph still deploys. |
| Dry-run shows `loop_detected` | The graph cycles. Bounded at 20 steps, so it stops rather than spinning. |
| Restored to the wrong content | The version number addressed a different snapshot than expected — check `GET /versions` before restoring. |
| Dry-run always takes the same branch | Tier order is fixed: expression edges beat llm edges beat unconditional, unless an edge sets its own `priority`. |

## Security

- `definition` and dry-run output are customer content. This module logs ids and
  the deploy target name only.
- Dry-run previews include prompt text — treat a captured response body as
  customer content.
