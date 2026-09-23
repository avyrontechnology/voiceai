# Spec 0016: Retype `voice.session` (third engine-retype slice)

## Goal

Annotate the `voiceai/modules/voice/session/` tree (composition, config,
prompts, turn/* — ~147 errors across 8 files, 93 in `turn/transcript_listener.py`
alone) to zero mypy errors and drop its `ignore_errors` override. Same playbook
as specs 0013/0014: annotations, narrows, and guards only.

## Non-goals

- No behavior change: no turn-pipeline logic rewrites, no listener/generation
  restructuring, no test changes. The God-object facades (`ListenerSession`,
  `TaskManager` delegation targets) get declarations, not redesigns.
- `DefaultInputHandler` vs `CallInputPort` B0 drift stays pinned (spec-0013
  note) unless annotation closes it for real (as happened with
  `SynthesizerPool` in spec-0014).

## Interface contracts

Changed files: the 8 `voice/session/**` files with errors plus this spec.
Expected fix classes (from the survey):

- Facade attribute declarations (`ListenerSession` dynamic attrs → class-level
  annotations; `TaskManager` narrow-delegator targets gain precise types).
- `x = None` later reassigned → `x: T | None = None`.
- Unannotated `{}`/`[]` attributes → precise annotations.
- `len()`/indexing on `Optional` → narrow first (no new raises on reachable paths).
- `CallArgs`/`CallConfig` construction-site mismatches → call-site annotations.

## Data model

None.

## Security notes

None (`make sec` scope unchanged).

## Test plan

- `mypy` zero on the tree with the override removed (strict temp config AND the
  project config, fresh cache).
- Full `make check` green — the turn/transcript/language characterization
  suites are the canary for this tree.

## Verification

```sh
make check
make sec
```

## Rollout

Single merge. No flags, no migrations.

## Burn-down

- [x] `voice.io.*` override removed (spec 0013).
- [x] `voice.tts.*` override removed (spec 0014).
- [x] `voice.session.*` override removed (this spec).
- [ ] `voice.asr.*` — spec 0017.
