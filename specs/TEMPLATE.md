# Spec NNNN — <short title>

- **Status:** draft | in progress | done | superseded by NNNN
- **Branch:** `<branch>` (base: `<base>`)
- **Owner:** <name>
- **Depends on:** spec NNNN / nothing

## Goal
One paragraph: the user-visible outcome. If you cannot state it in three sentences, split the spec.

## Non-goals
What this spec deliberately does not do (prevents scope creep during implementation).

## Design
The module(s) touched, new files by their canonical names, and data flow
(controller → service → repository). Note every AGENTS.md rule the design leans on.

## Interface contracts
Exact public signatures (functions, models, routes, collection names). This section is the
hand-off to implementing agents: parallel agents own disjoint files and code only against what
is written here. Changing a contract means editing the spec first, then the code.

## Data model
New/changed models. Every persisted model inherits `database.base.BaseFields`; every
collection/table name comes from `database.constants.Collections`.

## Security notes
Input validation at the controller boundary, authz decision point in the service, secrets and
log-redaction impact, outbound URLs, rate/abuse considerations. "None" must be argued, not assumed.

## Test plan
Behaviors to prove per module (unit via DI fakes, controller via ASGI transport). Offline only.

## Verification
The exact commands that must pass (`make check`, `make sec`, anything extra).

## Rollout
Migration/compat notes: what existing code starts using this, what gets deleted, revert plan.
