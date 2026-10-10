# Spec 0078: automation module (retire the graphs / workflows / campaigns families)

## Goal

Move the automation cluster out of the legacy `platform` bucket into one module,
`voiceai/modules/automation/`: graphs, workflows, and campaigns, plus their
version and run children.

Fifth and **largest** family of the composition-root strangler — 26 routes and 7
collections, against 6/5/3/5 for the four before it. This spec exists to land
that work as one coherent cutover rather than three interlocking partial ones.

## Why one module, not three

The three families are not separable:

- A `WorkflowCampaign` names a `workflow_id` — campaigns cannot exist without
  workflows.
- A `WorkflowVersion` belongs to a workflow; a `WorkflowRun` belongs to a
  version. The version chain is `workflow → version → run`.
- The legacy router carries **68 cross-references** between these identifiers.

Migrating them separately would recreate exactly the coupling seam that
`numbers` had with the voice inbound lookup (spec 0074) — the seam that cost a
silent production bug to find. One module keeps the chain inside one place.

## What makes this harder than the previous families

The previous four were flat CRUD. This one is **nested and versioned**:

| Document | Key | Children |
|---|---|---|
| `GraphDoc` | `graph_id` | `graph_versions` (many, ordered) |
| `WorkflowDoc` | `workflow_id` | `workflow_versions` (many, ordered) → `workflow_runs` (many) |
| `WorkflowCampaign` | `campaign_id` | references a `workflow_id`; carries `entries` |

Every write endpoint that touches a version bumps a number; the ordering of
versions is a real invariant (`list_graph_versions` sorts by version number, and
`list_workflow_versions` likewise). These are the behaviours a naive CRUD
translation would drop.

## Non-goals

- **Executing** anything. Deploy/run endpoints that actually dial or dispatch are
  out of scope — this spec moves the *records*, not the engine behind them.
- Migrating `batches` / `executions` (the calling families, own spec).
- Changing the campaign status machine. It moves verbatim.
- Any change to the version numbering scheme.

## Interface contracts

Module `voiceai/modules/automation/`, Rule 1 file set.

### Models (`models.py`)

Seven documents become `BaseFields`:

- `GraphDoc`, `GraphVersion`
- `WorkflowDoc`, `WorkflowVersion`, `WorkflowRun`
- `WorkflowCampaign`, `CampaignEntry`, `CampaignStats`

`id` replaces each family's legacy id field; the API keeps the legacy names.

`WorkflowCampaign.status` keeps its **enum** (`draft|scheduled|running|completed|
stopped`) — it is a state machine, not a free string, and flattening it would
lose that.

### Repository (`repository.py`)

Four repositories over the same tenant-scoped base — one per document family:
graphs (+ versions), workflows (+ versions), runs, campaigns. Reads go through
`raw_documents()` as in specs 0074–0077, so pre-migration `{payload: {...}}`
rows stay listable.

### Service (`service.py`)

Mirrors the legacy surface one method per endpoint. The behaviours that must
survive exactly:

- **Version numbering.** Publishing a new version assigns the next number for
  that parent; versions are listed ordered by it.
- **Campaign references.** Creating a campaign against an unknown `workflow_id`
  is a 404, not an orphan.
- **Campaign status transitions** are validated the way they are today.

### Controller (`controller.py`)

The 26 routes across four prefixes (`/graphs`, `/workflows`, `/workflow-campaigns`,
`/workflow-runs`), same paths, same scopes, same status codes.

## Data model

Collections kept as-is (`platform_graphs`, `platform_graph_versions`,
`platform_workflows`, `platform_workflow_versions`, `platform_workflow_runs`,
`platform_workflow_campaigns`). Renaming would orphan live automation records.

## Security notes

- Scopes unchanged: `platform:read` on reads, `platform:write` on mutations.
- `definition` (workflow) and `entries[].variables` (campaign) are open dicts of
  user data. They are **not** credentials, but they are customer content: not
  logged at INFO, only ids.
- Campaign entries carry `to_number` — PII. Logged by campaign id only.
- Delete is soft wherever the legacy delete is soft.

## Architecture delta

```
NEW  voiceai/modules/automation/{constants,models,errors,repository,service,
                                controller,schemas,static_methods,exceptions,
                                helpers,utils}.py + CONTRACT.md / README.md / RUNBOOK.md
MOD  voiceai/core/container.py       — automation services + container.wire
MOD  voiceai/modules/__init__.py     — automation registered in ALL_MODULES
MOD  voiceai/platform/router.py      — 4 routers removed
DEL  voiceai/platform/store.py       — the cluster's store methods
DEL  voiceai/platform/models.py      — the cluster's models
MOD  tooling/backfill_upstash_to_atlas.py — _MODULE_RESTORES entries
```

`test_route_inventory.py` is updated in the same spec.

## Test plan

- **version numbering**: publishing twice yields 1 then 2; the list is ordered.
- **campaign binding**: a campaign against an unknown workflow 404s.
- **campaign status transitions**: the valid moves pass, an invalid one is
  rejected.
- **soft deletes** per family.
- **legacy envelope reads** per family.
- **tenant isolation** per repository.
- controller: the 26 routes through the real app factory with `httpx`, plus the
  scope gate.
- **a route-count pin**: the module mounts exactly the legacy route set, so a
  dropped or invented endpoint fails the suite rather than shipping.

Coverage ≥85% on the new module.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Same shape as the previous four: module routers mount first, first match wins,
one commit, rollback is reverting it.

**Blast radius is the largest so far** — 26 routes and 7 collections move at once.
Mitigation is the test plan, not a flag: the legacy surface is reproduced exactly
and pinned by a route-count test, so the failure mode is a caught test rather
than a production surprise. If the team wants a staged rollout instead, this
spec is the one to split — it is the only remaining family big enough to warrant
it.

## Burn-down

- [ ] survey: coupling map and route inventory pinned
- [ ] `automation/constants.py`, `models.py`, `errors.py`
- [ ] `automation/static_methods.py` (version numbering), `helpers.py`, `utils.py`
- [ ] `automation/repository.py` (4 repositories), `service.py`
- [ ] `automation/schemas.py` + `controller.py` (26 routes)
- [ ] `automation/exceptions.py` + `CONTRACT.md` / `README.md` / `RUNBOOK.md`
- [ ] container providers + `container.wire` + module registry
- [ ] `platform/{router,store,models}.py`: cluster removed
- [ ] backfill: `_MODULE_RESTORES` entries
- [ ] `test_route_inventory.py` + auth shim `REMOVED_BY_SPEC`
- [ ] `test_repository_store.py` fixture moved off this family
- [ ] tests: versioning, campaign binding, status machine, storage, controller
- [ ] `make check` + `make sec` green
