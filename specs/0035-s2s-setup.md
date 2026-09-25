# Spec 0035: S2S setup validation move (M5 slice 6)

## Goal

Move `TaskManager.__setup_s2s` verbatim to
`voice/session/s2s_runner.py`, leaving a thin delegator. The method validates
the S2S config and arms the stream-ready event — S2S-region code beside the
runner that consumes it.

## Non-goals

- Any other TaskManager region (`__await_stream_sid`,
  `wait_for_current_message`, health reporting, S2S conversation remainder).
- Signature or semantics changes (verbatim move, quirks preserved).
- Deleting the delegator (TaskManager stays API-stable for legacy callers).

## Interface contracts

- `S2SConfig` surfaces on `agents/__init__.__all__` (bridge-4 legal seam —
  voice may import agents only through it).
- `s2s_runner.py::setup_s2s(session)` — verbatim body, session-first,
  `otobaai.voice` logger per move discipline.
- `TaskManager.__setup_s2s` becomes a one-line delegator.
- Tests: new `voiceai/modules/voice/tests/session/test_s2s_setup.py`
  drives the seam directly (fake session: parsed fields, event armed,
  invalid config raises); existing suites pass unchanged.

## Data model

None. No collections, no migrations.

## Security notes

- No payload logging (provider/model identifiers only, verbatim line).
- `make sec` clean.

## Test plan

New seam tests + `make check` (s2s suites exercise neighbors).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] S2S setup → `s2s_runner.py` + delegator + surface export + tests
  (voice budget 46900→47000 with ratchet-down note).
