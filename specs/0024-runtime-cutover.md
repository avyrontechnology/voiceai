# Spec 0024: agent runtime cutover — BrainFactory is the only brain path (M3)

## Goal

Retire the ~150 lines of verbatim legacy brain assembly in
`TaskManager.__get_agent_object`: every session builds conversation brains
through the injected `BrainFactory` (or a default-constructed one), and the
legacy graph/knowledgebase branches are deleted. Parity is pre-proven by
`tests/test_brain_factory_equivalence.py` — this spec spends that proof.

## Non-goals

- `agent_types/*` deletion; `brains/legacy_graph.py` deletion; the
  `task_manager.py:62` star-import (engine-import surface, T7 cutover).
- RAG `os.environ` side-channel removal (spec-0004 debt, stays pinned).
- `assistant.py` (no importers; T7 cleanup, not M3).
- Behavior changes beyond the one named below.

## Decisions (approved)

1. Strict flip, no fallback: `__get_agent_object` requires the `brain_factory`
   kwarg and raises a loud `RuntimeError` naming the spec when absent. A
   default-constructed fallback inside the legacy file would need a
   legacy→modules import (§3.1 violation); the only live producer
   (`voice/adapters/manager.py`) already injects, so strictness costs nothing
   in prod and fails fast everywhere else.
2. Unknown `agent_type`: `AgentsError` envelope replaces the `raise f`-string
   TypeError crash (never a contract; opacity rules demand the envelope).
3. Equivalence test rewritten in place as the factory-only pin.
4. Legacy-suite divergences fix-forward in-slice (the flip must prove itself).

## Interface contracts

**Slice 1 — the flip (this spec authorizes the legacy edit).**
- `agent_manager/task_manager.py::__get_agent_object`: factory-only body —
  missing kwarg raises `RuntimeError` naming spec 0024 (fail-fast, never
  silent); the verbatim branches delete. The legacy file gains no imports
  (kwargs-injection, §3.1 bridge 3).
- `tests/test_brain_factory_equivalence.py`: rewritten as the factory-only
  pin — identical configs per kind, side-channel write, `AgentsError` on
  unknown kind, `KeyError` parity on malformed tasks, plus a mechanical
  assertion that the verbatim assembly markers are gone from the legacy
  method.
- `make test-all` (minus AGENTS.md §8 known debt) proves the engine cannot
  tell the difference.

**Slice 2 — burn-down + tripwire.**
- Retire the spec-0015 bridge comments (the bridge is now the road);
  CONTRACT/RUNBOOK cutover notes; T7 remainder list.
- `tests/arch/test_runtime_cutover.py`: `task_manager.py` must not contain
  the verbatim assembly markers — the flip can never silently revert.

## Data model

None. No collections, no migrations.

## Security notes

- Unknown agent types now answer the opaque envelope instead of crashing
  with a string-raise (AGENTS.md §4, error opacity — an improvement).
- No secrets, no new outbound calls. `make sec` scope unchanged.

## Test plan

Slice 1: rewritten equivalence pin (factory-only) + full legacy suite.
Slice 2: tripwire + docs. Both: `make check` + `make sec` green.

## Verification

```sh
make check
make sec
make test-all
```

## Rollout

Single merge per slice; behavior-identical by proof. Rollback is revert
(the deleted branches are in git history with the equivalence test naming
them).

## Burn-down

- [ ] Slice 1: factory-only flip + equivalence rewrite.
- [ ] Slice 2: burn-down + docs + tripwire.
