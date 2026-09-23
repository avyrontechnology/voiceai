# Spec-Driven Development (SDD)

Binding loop from `AGENTS.md §1`. No implementation lands without a spec.

## The loop

1. **Spec** — new file `specs/NNNN-slug.md` from `specs/TEMPLATE.md`.
   States goal, non-goals, interface contracts, data model, security notes,
   test plan, verification (`make check`, `make sec`), rollout, burn-down.
2. **Architect pass** — a human or Plan agent attacks the spec before code exists.
   The spec is updated, not the code.
3. **Implement** — 2–4 agents with **disjoint file sets** stated in each prompt.
   Agents code only the spec's contracts, never edit files they do not own,
   and report deviations instead of improvising. One integrator merges and runs
   verification.
4. **Verify** — `make check` (lint + strict arch lint + mypy + tests) and
   `make sec` green; `make cov` ≥ 85% on touched modules for larger changes.
5. **Review** — `AGENTS.md §10` Definition of Done, then commit
   `type(scope): summary [spec-NNNN]`.

## Numbering

Sequential `NNNN` (`0000`, `0001`, …). One spec per change. Never reuse a number.
The spec id appears in commit subjects, TODOs (`# TODO(spec-NNNN): …`),
and legacy-shim tags (`# legacy-shim(spec-NNNN)`).

## Ownership

Ownership lives in the code: `voiceai/modules/__init__.py` (`ModuleDef`
`owner_squad`/`slack_channel`/`runbook_path`) rendered to `docs/OWNERSHIP.md`.
There are no CODEOWNERS files by team decision — the registry is truth and
`tests/arch/modules/test_registry.py` pins it.
