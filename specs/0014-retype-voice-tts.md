# Spec 0014: Retype `voice.tts` (second engine-retype slice)

## Goal

Annotate the `voiceai/modules/voice/tts/` tree (base, stream, pool, 12
providers — ~155 errors across 15 files) to zero mypy errors and drop its
`ignore_errors` override. Same playbook as spec 0013 (`voice.io`): annotations,
narrows, and guards only.

## Non-goals

- No behavior change: no provider-logic rewrites, no synthesis-path changes, no
  test changes. Any site where typing would alter runtime semantics gets a
  shape-preserving construct instead.
- `SynthesizerPool` vs `SynthesisPoolPort` B0 drift stays pinned (spec-0011
  note) — resolving it needs a runtime/port decision, not annotations.

## Interface contracts

Changed files: the 15 `voice/tts/**` files with errors plus this spec. Expected
fix classes (from the survey — same families as `voice.io`):

- `x = None` later reassigned → `x: T | None = None`.
- Unannotated `{}`/`[]` attributes → precise annotations.
- `len()`/indexing on `Optional` → narrow first (no new raises on reachable paths).
- Async-generator `generate` overrides → `AsyncGenerator` return types.
- Base hooks returning `None` where providers return payloads → `-> Any` to
  match override contracts (spec-0013 precedent: `form_*_message`).

## Data model

None.

## Security notes

None (`make sec` scope unchanged).

## Test plan

- `mypy` zero on the tree with the override removed (strict temp config AND the
  project config).
- Full `make check` green (covers the TTS characterization + relocation suites).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge. No flags, no migrations.

## Burn-down

- [x] `voice.io.*` override removed (spec 0013).
- [x] `voice.tts.*` override removed (this spec).
- [ ] `voice.asr.*`, `voice.session.*` — one spec each.
