# Spec 0078: automation cluster — survey and split plan

## Status: SURVEY COMPLETE, SCOPE SPLIT REQUIRED

This spec was written as one cutover and the survey proved that wrong. It is
kept as the record of what was found, and **replaced by the split below**. Do
not implement it as written.

## What the survey found

The cluster is **26 routes and ~25 models across 7 collections**, not the 9
models first estimated. More importantly, it is not a CRUD family:

- `platform/graphs.py` (221 lines) holds `GraphDefinition` and
  `graph_to_agent_payload`.
- `platform/workflows.py` (317 lines) holds `WorkflowDefinition` and
  `run_workflow` — the **execution engine**.
- `deploy_graph` calls `graph_to_agent_payload`; `test_run` calls `run_workflow`.

So the endpoints are not just moving records: **two of them run the automation**.
Migrating "the CRUD" while leaving the engine in `platform/` would recreate the
numbers→voice seam (spec 0074) in a worse form — a module whose routes call into
legacy for its actual behaviour.

538 lines of engine code does not belong in a migration commit whose purpose is
"move the records".

## The split

| Spec | Scope | Why it stands alone |
|---|---|---|
| **0078** | Graphs: `GraphDoc`, `GraphVersion`, their routes | Record storage + versioning. `deploy_graph` is left mounted on the legacy router until 0079, so this spec has no engine dependency. |
| **0079** | `platform/graphs.py` → the automation module (`GraphDefinition`, `graph_to_agent_payload`) | The graph engine. Separate because it is behaviour, not storage. |
| **0080** | Workflows: `WorkflowDoc`, `WorkflowVersion`, `WorkflowRun` | Record storage + the version chain. |
| **0081** | `platform/workflows.py` → the automation module (`WorkflowDefinition`, `run_workflow`) | The workflow execution engine. |
| **0082** | Campaigns: `WorkflowCampaign`, entries, stats, the status machine | Depends on workflows (0080); moves after it. |

Ordering rationale: **storage before engine** for each pair, so the engine
migration never has to reach back into the platform store for records. The two
domains (graph, workflow) are independent of each other, so 0079–0081 can be
sequenced by whoever is free.

## Coupling (unchanged from the survey, still the reason one module)

- A `WorkflowCampaign` names a `workflow_id`; campaigns cannot exist without
  workflows — hence 0082 after 0080.
- A `WorkflowVersion` belongs to a workflow; a `WorkflowRun` to a version.
- The legacy router carries **68 cross-references** between these identifiers.

The end state is still one module (`voiceai/modules/automation/`); the split is
about how it gets there, so each step is reviewable and revertable on its own.

## Original scope (kept for reference)

- 26 routes across `/graphs`, `/workflows`, `/workflow-campaigns`, `/workflow-runs`
- 7 collections: `platform_graphs`, `platform_graph_versions`, `platform_workflows`,
  `platform_workflow_versions`, `platform_workflow_runs`, `platform_workflow_campaigns`
- Version numbering, campaign binding and the campaign status machine are the
  behaviours that must survive every step.

## Deviations

- The first estimate said ~9 models; the survey found ~25. Recorded rather than
  quietly corrected, because it is the reason the estimate was not safe to plan
  against.
- The estimate said "executing anything is out of scope". That was wrong:
  `deploy_graph` and `test_run` execute. Correcting it is what produced the split.

