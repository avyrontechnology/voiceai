# Spec 0037: playout drain wait move (M5 slice 8)

## Goal

Move `TaskManager.wait_for_current_message` verbatim to
`voice/session/turn/output_loop.py`, leaving a thin delegator. The method
drains synth-pipeline playout by watching mark events (flush wait, empty
drain, pre-mark skip, plivo two-item quirk, final-chunk break) — output-loop
territory beside the playout bodies.

## Non-goals

- Any other TaskManager region (health reporting, S2S conversation
  remainder).
- Signature or semantics changes (verbatim move, quirks preserved).
- Deleting the delegator (mangled pins + teardown tests replace the method
  on instances — dispatch must stay stable).

## Interface contracts

- `output_loop.py::wait_for_current_message(session)` — verbatim body,
  session-first, `otobaai.voice` logger per move discipline. Protocol gains
  `mark_event_meta_data`; `asyncio`/`time`/logger already imported.
- `TaskManager.wait_for_current_message` becomes a one-line delegator.
- Tests: new `voiceai/modules/voice/tests/session/turn/test_message_wait.py`
  drives the seam directly (flush wait, empty drain, pre-mark skip, plivo
  quirk, final-chunk break, timeout warning); legacy teardown/barge-in
  suites pass unchanged (they replace or await the method).

## Data model

None. No collections, no migrations.

## Security notes

- One INFO line with the mark list (verbatim, identifiers only).
- `make sec` clean.

## Test plan

New seam tests + `make check` (teardown suites replace/await the method).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Playout drain wait → `turn/output_loop.py` + delegator + tests
  (voice budget 47100→47200 with ratchet-down note).
