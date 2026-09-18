# Specs — spec-driven development (SDD)

Every iteration on this codebase starts here. The loop:

1. **Spec** — copy `TEMPLATE.md` to `NNNN-short-slug.md` (next number), fill every section.
   No implementation without a spec; a bugfix under ~20 lines may reference the spec of the
   feature it fixes instead of a new one.
2. **Architect pass** — have an architect (human or the Plan agent) attack the spec: layer
   violations, missing security notes, contracts that will not integrate. Fold the deltas in.
3. **Implement** — build exactly the contracts in the spec, following `AGENTS.md`. When
   orchestrating multiple agents: each agent owns a disjoint file set, codes only against the
   spec's contracts, and never edits another agent's files; one integrator merges and verifies.
4. **Verify** — `make check` and `make sec` green. New behavior has tests per module.
5. **Review + commit** — walk the Definition of Done in `AGENTS.md`; commit as
   `type(scope): summary [spec-NNNN]`.

Status lifecycle: `draft → in progress → done` (or `superseded by NNNN`). Keep specs updated
when reality diverges — the spec is documentation of record, not a proposal artifact.

| Spec | Title | Status |
|------|-------|--------|
| 0001 | Architecture foundation (common, core, database, health module) | done |
| 0002 | Agents module — definition domain, models split, brains, CRUD (tranche A) | done |
| 0003 | Database driver selection + real backend for `core/db.py` | done |
| 0004 | Voice module — realtime runtime strangler out of TaskManager (tranche B) | done |
| 0005 | Platform auth migration into `modules/auth` | draft |
