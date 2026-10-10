# Spec 0079: workflows module (retire the workflows family)

## Goal

Move the workflows family — records **and** engine — into a new module,
`voiceai/modules/workflows/`, with workflow definitions, their version chain,
workflow runs, campaigns, and the routes that invoke them.

Follows the one-unit rule established by spec 0078: **a family whose endpoints
call its own engine moves as one unit.**

## What this family is

16 routes across three prefixes, plus a 317-line engine:

| Prefix | Routes | Engine? |
|---|---|---|
| `/workflows` | 7 (CRUD + versions + restore) | no |
| `/workflows/{id}/validate` | 1 | **yes** — `validate_workflow` |
| `/workflows/{id}/test-run` | 1 | **yes** — `run_workflow` |
| `/workflow-runs` | 1 (get) | no |
| `/workflow-campaigns` | 6 | **yes** — `run_campaign` |

- `platform/workflows.py` (317 lines): `WorkflowDefinition`, `validate_workflow`,
  `run_workflow`, `run_campaign`.

## The difference from graphs, and why campaigns move too

Unlike the graph engine, **this engine is not pure.** `run_workflow` and
`run_campaign` take a `MemoryStore` and read/write through it:

- `run_workflow` persists each run (`store.save_workflow_run`) and, on an
  `agent` node, calls **`run_simulated_call(store, ...)`** — which belongs to the
  **calls** family (`/calls`, still legacy), not to workflows.
- `run_campaign` loads the campaign, loads its workflow, sets `status =
  RUNNING`, writes `stats`, then iterates entries calling `run_workflow` per
  entry — re-reading the campaign between entries so a **stop** is honoured
  mid-run.

So the engine holds a *storage* dependency, not just a compute one. Moving the
workflow records without the engine would leave `test-run` reading from a store
that no longer has the workflows — the same split brain that killed the graphs
"storage first" attempt (spec 0078).

**Therefore campaigns move in this spec too**, not separately as originally
planned. `run_campaign` cannot be split from the records it drives, and
campaigns are what it drives.

### The calls dependency, and what it means for ordering

`run_workflow`'s `agent` node calls `run_simulated_call`, which is the calls
family — `/calls`, still legacy. Two ways out:

1. **Keep the store port wide enough to include what the engine uses**, including
   `run_simulated_call`, and migrate calls separately afterwards. The engine
   keeps a dependency on a legacy surface, but through a declared port rather
   than an implicit import.
2. **Migrate `/calls` first**, so the workflow engine can call the new
   executions module instead of `platform.simulation`.

Option 1 is what this spec does, and it is honest about the cost: the engine's
port carries a `run_simulated_call` seam until the calls family migrates. That
seam is the *same shape* of seam spec 0078 rejected — a module engine reaching
into legacy — and it is acceptable here only because it is declared, narrow, and
removed by a named follow-up, not left open-ended. **The alternative is doing
`/calls` first**, which is a smaller, self-contained family and removes the seam
entirely rather than documenting it.

## Scope

### Records

- `WorkflowDoc` (`workflow_id`, `name`, `definition`, timestamps)
- `WorkflowVersion` (`version_id`, `workflow_id`, `version_number`, `name`,
  `definition`, `note`, `created_at`)
- `WorkflowRun` (`run_id`, `workflow_id`, `campaign_id`, `contact`, `status`,
  `reports`, `started_at`, `ended_at`)

All three become `BaseFields`. `id` replaces the legacy id field; the API keeps
the legacy names.

### Engine (`platform/workflows.py` → `modules/workflows/engine.py`)

Moved with **one deliberate change**: `run_workflow` / `run_campaign` take a
store *port* instead of `MemoryStore`. The port is the narrow slice they already
use — get/save workflow, campaign and run. That is what lets the engine be unit
tested without a database, and what stops the module's engine reaching back into
`platform`.

### Campaign status machine

`draft → scheduled → running → completed/stopped`, an **enum**, moved verbatim.
`start_campaign` refuses to start anything not in `draft|scheduled`.

## Behaviours that must survive

- **Version numbering is `len(existing) + 1`**, exactly as graphs — and the same
  reason: restore URLs address snapshots by number.
- **Restore snapshots the live state first**, same as graphs.
- **Campaign binding**: a campaign against an unknown `workflow_id` is a 404.
- **Stop is honoured mid-run**: `run_campaign` re-reads the campaign between
  entries. This is the one genuinely stateful behaviour in the family and it is
  pinned by test.
- **An invalid definition is a 422** with the shared opaque `ValidationResult`
  shape (already in `common/validation.py` since spec 0078).

## Data model

Collections kept as-is: `platform_workflows`, `platform_workflow_versions`,
`platform_workflow_runs`, `platform_workflow_campaigns`. Renaming would orphan
live automation records.

## Security notes

- Scopes unchanged: `platform:read` on reads, `platform:write` on the rest.
- `definition`, `contact` and `entries[].variables` are customer content.
  `contact` in particular can hold a phone number (PII) — logged by run id only,
  never the contact.
- `run_campaign` **places outbound calls** through the voice path. That is
  unchanged behaviour; this spec moves where the code lives, not what it does.
  `start_campaign` remains the guard that keeps an operator from dialling a list
  by accident, and it is not being weakened.

## Architecture delta

```
NEW  voiceai/modules/workflows/ — models, repository, engine, service,
     controller, schemas, constants, errors and module docs
MOD  voiceai/core/container.py   — workflow services + container.wire
MOD  voiceai/modules/__init__.py — workflows registration
MOD  voiceai/platform/router.py  — workflows/runs/campaigns routers removed
DEL  voiceai/platform/{store,models}.py workflow + campaign methods
DEL  voiceai/platform/workflows.py (317 lines)
MOD  tooling/backfill_upstash_to_atlas.py — _MODULE_RESTORES entries
```

## Test plan

- version numbering and restore, as graphs.
- **campaign binding**: unknown workflow → 404.
- **status machine**: start refuses a non-draft campaign; stop is honoured
  mid-run (the stateful one).
- **engine parity**: `validate_workflow` output matches the legacy function.
- **store port**: the engine runs against a fake port, proving it does not reach
  for `MemoryStore`.
- soft deletes, legacy-envelope reads, tenant isolation, per family.
- controller: all 16 routes through the real app factory with `httpx`.
- **route-count pin**: the module mounts exactly the legacy route set.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

One commit, rollback is reverting it. **Higher stakes than any family so far:**
`run_campaign` places real outbound calls. The test plan is the mitigation —
parity tests plus the mid-run-stop test — not a flag.

## Deviations

- The draft said to fold workflows into `modules/automation`. The architect pass
  rejected that: automation has 1,841 Python lines against a 3,000-line budget,
  and a faithful 16-route implementation would risk exceeding it while mixing
  two unrelated automation domains in one module. A standalone workflows module
  preserves Rule 1, the registry budget, and one responsibility per file.
- Campaigns were planned as spec 0080 and are folded in here. The engine reads
  them, so they cannot move independently; leaving them behind would recreate the
  split brain spec 0078 documents.
- The engine is not pure, unlike the graph engine: `run_workflow` and
  `run_campaign` take a store. That dependency is narrowed to an explicit port
  rather than left reaching into `MemoryStore`, which is the only way the module
  ends up with an engine it can actually test.
