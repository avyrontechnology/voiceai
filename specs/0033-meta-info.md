# Spec 0033: turn meta-info identity move (M5 slice 4)

## Goal

Move TaskManager's turn/response identity pair (`__get_updated_meta_info`,
`_spawn_followup_meta_info`) verbatim into a new
`voice/session/turn/meta_info.py`, leaving thin delegators. The pair stamps
sequence/turn/response-group identity for transcript and tool-call grouping —
one cohesive concern, pinned across e2e, s2s, and session suites.

## Non-goals

- Any other TaskManager region (language instruction, stream-sid wait,
  s2s setup, message waiting, health reporting, S2S remainder).
- Signature or semantics changes (verbatim move, quirks preserved: the
  turn_id/sequence_id/chunk_id distinction comment, followup key drops,
  VOICEAI_TRACE_META log lines verbatim).
- Deleting the delegators (TaskManager stays API-stable for legacy callers,
  including mangled-name dispatch).

## Interface contracts

- New `voiceai/modules/voice/session/turn/meta_info.py`: `get_updated_meta_info(session,
  meta_info=None)` + `spawn_followup_meta_info(session, meta_info)`,
  session-first, `otobaai.voice` logger per move discipline. No new imports
  beyond stdlib (`uuid`) + `common.logger` — the bodies touch only session
  state and stdlib.
- `TaskManager` methods become one-line delegators.
- Tests: new `voiceai/modules/voice/tests/session/turn/test_meta_info.py`
  drives the seam directly (fake session: sequence/turn/group stamping,
  followup key drops + parent linkage, None-meta_info transcriber fallback);
  existing e2e/s2s/session suites pass unchanged through the delegators.

## Data model

None. No collections, no migrations.

## Security notes

- Log lines carry identifiers/counters only (verbatim, no payloads).
- `make sec` clean.

## Test plan

New seam tests + full `make check` (e2e/s2s suites exercise the delegators).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Meta-info pair → `turn/meta_info.py` + delegators + tests
  (voice budget 46600→46800 with ratchet-down note).
