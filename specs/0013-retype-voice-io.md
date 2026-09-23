# Spec 0013: Retype `voice.io` (first engine-retype slice)

## Goal

Annotate the `voiceai/modules/voice/io/` tree (input/output handlers +
telephony providers, 8 files, 37 errors) to zero mypy errors and drop its
`ignore_errors` override — the first burn-down payment on spec-0011's scoped
trees (`asr`, `tts`, `session` follow as their own specs).

## Non-goals

- No behavior change: annotations, narrows, and guards only. Any site where
  typing would alter runtime semantics gets a shape-preserving construct
  (`x or default`, explicit `None` checks matching existing handling) instead.
- No provider-logic rewrites; no test changes unless a test pins an untyped
  seam (then the seam, not the test, is annotated).

## Interface contracts

Changed files: the 8 `voice/io/**` files with errors
(`output/telephony_providers/freeswitch.py`, `output/telephony.py`,
`input/default.py`, `input/telephony.py`, `output/default.py`,
`input/telephony_providers/{sip_trunk,plivo}.py`,
`output/telephony_providers/sip_trunk.py`) plus this spec. Fix classes:

- `x = None` later reassigned → `x: T | None = None`.
- Unannotated `{}`/`[]`/`set()` attributes → precise annotations.
- `len()`/indexing on `Optional` → narrow first (no new raises on reachable paths).
- Functions that never return a value → `-> None`.
- Third-party `plivo` import without stubs → targeted `ignore[import-untyped]`
  (same treatment siblings already use, if any; otherwise local ignore with reason).

## Data model

None.

## Security notes

None (no I/O, auth, or secret handling changes; `make sec` scope unchanged).

## Test plan

- `mypy` zero on the tree with the override removed (checked with the strict
  temp config AND the project config).
- Existing io suites green: full `make check` (which runs every colocated +
  arch test, including the telephony characterization suites).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge. No flags, no migrations.

## Burn-down

- [x] `voice.io.*` override removed (this spec).
- [ ] `voice.asr.*`, `voice.tts.*`, `voice.session.*` — one spec each.
- [ ] B0 drift surfaced by real analysis (not fixable by annotation without
  runtime/port changes): `DefaultInputHandler` vs `CallInputPort` keeps its
  `type: ignore[assignment]` pin until the handler or the port is fixed
  (`SynthesizerPool` vs `SynthesisPoolPort` resolved for real in spec-0014 —
  annotation closed the drift and the pin is gone).
