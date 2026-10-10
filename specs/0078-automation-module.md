# Spec 0078: graphs — records and engine together (retire the graphs family)

## Status: ORDER REVERSED AFTER AN IMPLEMENTATION ATTEMPT

This spec was first written as "storage first, engine second" (0078 records,
0079 engine). That order was implemented, and it **produces a split brain, not
a seam**. It was reverted. This is the corrected spec.

## What the failed attempt showed

Implementing "records into the module, engine stays on the legacy router" left
the three engine routes (`/validate`, `/dry-run`, `/deploy`) depending on
`MemoryStore` for their graph, while every new record route wrote through the
automation module. Two readers, two stores, one collection name:

- A graph created through the **module** was invisible to the engine routes.
- A graph created through the **legacy store** was invisible to the module.

That is the same shape as the numbers→voice problem (spec 0074), where a scoped
repository left one tenant's inbound calls unresolvable — except here it is
*every* graph, and the failure would be silent until someone reported a
missing graph. There is no safe moment where two readers disagree about where
graphs live.

## The rule this establishes

**A family whose endpoints call its own engine moves as one unit.**

The four families already migrated (numbers, knowledge, webhooks, integrations)
were flat CRUD: their endpoints only touched storage, so storage alone was a
complete move. `graphs` cannot: `validate`, `dry-run` and `deploy` *execute*.
Splitting records from engine leaves a live reader pointed at a store that no
longer holds the data.

This is the migration-ordering lesson, and it generalises to every remaining
family whose routes do real work.

## Goal

Move the graphs family — records **and** engine — into one module,
`voiceai/modules/automation/`, in a single cutover. No seam, no second reader.

## Scope

### Records (`GraphDoc`, `GraphVersion`)

Both become `BaseFields` documents, replacing the legacy `{payload: ...}`
envelope.

- `GraphDoc`: `name`, `agent_id | None`, `definition: dict`, timestamps.
- `GraphVersion`: `graph_id`, `version_number: int`, `name`, `definition`,
  `note | None`, `created_at`.

`id` replaces `graph_id` / `version_id`; the API keeps the legacy field names.

### Engine (`platform/graphs.py`, 221 lines)

`GraphDefinition`, `validate_definition`, `dry_run` and `graph_to_agent_payload`
move verbatim into the module. `_parse_definition`'s opaque 422 (which carries
dotted locations and pydantic error *types* only, never validator `msg` text —
spec 0052, error opacity) moves with it, because that error shape is a contract.

### Routes — all ten, together

| Route | Engine | Shape |
|---|---|---|
| `POST /graphs` | no | 201, `GraphDoc` |
| `GET /graphs` | no | `GraphListResponse` |
| `GET /graphs/{id}` | no | `GraphDoc` |
| `PUT /graphs/{id}` | no | `GraphDoc` |
| `DELETE /graphs/{id}` | no | `DeletedResponse` |
| `GET /graphs/{id}/versions` | no | `GraphVersionListResponse` |
| `POST /graphs/{id}/restore/{n}` | no | `GraphDoc` |
| `POST /graphs/{id}/validate` | **yes** | `ValidationResult` |
| `POST /graphs/{id}/dry-run` | **yes** | `DryRunResult` |
| `POST /graphs/{id}/deploy` | **yes** | agent payload JSON |

Same paths, same scopes, same status codes. No route added or removed.

## The versioning contract

Version numbering is `len(existing) + 1` — computed, not `max + 1`, exactly as
the legacy store did. `POST /{id}/restore/{n}` addresses snapshots by number, so
renumbering would repoint every existing restore URL at a different snapshot,
permanently. Lists are ordered by number.

Restore snapshots the **live state first** (`note: before restore of v{n}`), then
copies the target's name and definition over the graph. That is what makes a
restore reversible, and it is pinned by test.

## Data model

Collections kept as-is: `platform_graphs`, `platform_graph_versions`. Renaming
would orphan live graphs. Legacy `{payload: {...}}` rows stay listable through
`raw_documents()` (specs 0074–0077).

## Security notes

- Scopes unchanged: `platform:read` on reads, `platform:write` on mutations,
  `agents:write` on deploy (that one is different and moves verbatim).
- `definition` is customer content, not a credential: logged by id only, never
  dumped.
- The 422 detail must stay opaque — dotted locations and error *types* only,
  never validator `msg`, which can embed input.
- Delete is soft, as today.

## Test plan

- **version numbering**: snapshot twice yields 1 then 2; the list is ordered.
- **restore**: snapshots before restoring, copies name + definition, 404 on an
  unknown version number.
- **engine parity**: `validate`, `dry_run`, `deploy` produce byte-identical
  output to the legacy functions, including the 422 shape.
- soft delete; legacy-envelope read; tenant isolation.
- controller: all ten routes through the real app factory with `httpx`.
- **a route-count pin**: the module mounts exactly ten routes, so a dropped or
  invented endpoint fails the suite.

Coverage ≥85% on the new module.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

One commit, rollback is reverting it. Module routers mount first, first match
wins. Unlike the previous four this is a **behaviour-bearing** migration — the
engine executes — so the parity tests above are the mitigation, not a flag.

## Remaining cluster (unchanged by this spec)

- **0079** — workflows: `WorkflowDoc` / `WorkflowVersion` / `WorkflowRun` plus
  `platform/workflows.py` (`WorkflowDefinition`, `run_workflow`) plus their
  routes, same one-unit rule.
- **0080** — campaigns: `WorkflowCampaign`, entries, stats, the status machine.
  Depends on workflows, so it follows 0079.

Coupling across the cluster is unchanged and is why it stays one module: a
campaign names a `workflow_id`, a version belongs to a workflow, a run belongs
to a version, and the legacy router carries **68 cross-references** between
these identifiers.

## Burn-down

- [x] `automation/constants.py`, `models.py`, `errors.py`
- [x] `automation/engine.py` — the engine, verbatim from `platform/graphs.py`
- [x] `automation/static_methods.py` (version numbering), `helpers.py`, `utils.py`
- [x] `automation/repository.py`, `service.py` (incl. the shared definition parse)
- [x] `automation/schemas.py` + `controller.py` — all **ten** routes
- [x] `automation/exceptions.py` + `CONTRACT.md` / `README.md` / `RUNBOOK.md`
- [x] `common/validation.py` — `ValidationResult`, shared with the workflow engine (Rule 2)
- [x] container provider + `container.wire` + module registry
- [x] `platform/{router,store,models}.py` + `platform/graphs.py` (deleted, 221 lines)
- [x] backfill: `_MODULE_RESTORES` entry
- [x] `test_route_inventory.py` (45 platform routes), `test_registry.py`,
      `test_tenant_isolation.py`, auth shim `REMOVED_BY_SPEC` (11 names)
- [x] `test_repository_store.py` fixture moved graphs → workflows
- [x] tests: engine parity (15), service/storage/versioning/route-count (18)
- [x] `make check` green (2331 passed) + `make sec` clean

## Deviations

- The first estimate said ~9 models; the survey found ~25. Recorded rather than
  quietly corrected, because it is why the estimate was not safe to plan against.
- The first estimate said "executing anything is out of scope". Wrong:
  `deploy_graph` executes. Correcting it is what produced the split.
- **Storage-before-engine was implemented, found to create a split brain, and
  reverted.** Recorded here rather than quietly rewritten, because "storage
  first" is the intuitive order and the failure only shows up once a graph is
  created through one route and read through another.
- **`ValidationResult` moved to `common/validation.py`.** It was discovered
  mid-implementation that `platform/workflows.py` imports it from
  `platform/graphs.py` — the workflow engine importing a *graph* module for a
  shape with nothing to do with graphs. Rule 2 (something two modules need
  moves to common) applied, which also means the cross-family import does not
  outlive this migration when workflows move in 0079.
- **`NodeReport` stayed in the legacy models.** It is referenced by
  `WorkflowRun` — a workflow engine type — so removing it with the graph
  family would have broken workflows, which are still legacy (0079).
- **`test_repository_store.py` moved graphs → workflows.** Fifth family to
  serve as that file's fixture; workflows is the next still in the store with
  a full CRUD shape.
