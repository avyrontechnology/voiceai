# Spec 0017: Retype `voice.asr` (last engine-retype slice)

## Goal

Annotate the `voiceai/modules/voice/asr/` tree (base, pool, 12 providers +
deepgram sessions — ~400 errors, the largest slice) to zero mypy errors and
drop the last `ignore_errors` override. Same playbook as specs 0013/0014/0016:
annotations, narrows, and guards only. Closes the spec-0011 scoping entirely.

## Non-goals

- No behavior change: no provider-logic rewrites, no audio-path changes, no
  test changes. Third-party SDK imports without stubs are left alone unless the
  project config flags them (spec-0013 plivo precedent: it does not).
- Transcriber-pool switch/reconnect semantics untouched (concurrency-critical).

## Interface contracts

Changed files: the `voice/asr/**` files with errors plus this spec. Expected fix
classes (provider skeletons mirror the tts slice):

- `x = None` later reassigned → `x: T | None = None` (connections, tasks,
  timestamps, request ids).
- Unannotated `{}`/`[]` attributes → precise annotations.
- `len()`/indexing on `Optional` → narrow first (no new raises on reachable paths).
- Async-generator `receive` loops declared `-> None` → `AsyncGenerator`.
- Base hooks returning `None` where providers return payloads → match override
  contracts (spec-0014 `form_*_message` precedent).

## Data model

None.

## Security notes

None (`make sec` scope unchanged).

## Test plan

- `mypy` zero on the tree with the override removed (strict temp config AND the
  project config, fresh cache — incremental staleness produced phantom errors
  in spec-0014, so ground-truth runs are `--no-incremental`).
- Full `make check` green (Deepgram golden + transcriber-pool suites are the
  canary for this tree).

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
- [x] `voice.session.*` override removed (spec 0016).
- [x] `voice.asr.*` override removed (this spec — scoping fully paid down).
- [x] Closeout verified: `make check` green (1392 passed, mypy 0 over 390 files,
  bandit clean) with zero `ignore_errors` remaining; third-party stub gaps
  adjudicated per file, none blanket-ignored. `docs/CHECKPOINT.md` resume table
  retired with this note.
