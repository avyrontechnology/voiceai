# Spec 0036: stream-sid wait move (M5 slice 7)

## Goal

Move `TaskManager.__await_stream_sid` verbatim to
`voice/session/welcome.py`, leaving a thin delegator. The method gates the
welcome message on the carrier stream id (timeout → end-of-conversation
fallback) — pre-welcome setup beside the first-message senders.

## Non-goals

- Any other TaskManager region (`wait_for_current_message`, health
  reporting, S2S conversation remainder).
- Signature or semantics changes (verbatim move, quirks preserved: timeout
  fallback, stream connect report, handler handoff).
- Deleting the delegator (mangled-name dispatch + legacy pins stay stable).

## Interface contracts

- `welcome.py::await_stream_sid(session, timeout=10.0)` — verbatim body,
  session-first, `otobaai.voice` logger per move discipline. No new imports
  beyond stdlib (`asyncio`, `time`) — the body touches session state only.
- `TaskManager.__await_stream_sid` becomes a one-line delegator.
- Tests: new `voiceai/modules/voice/tests/session/test_welcome_gate.py`
  drives the seam directly (fake session: sid handoff to output handler,
  timeout fallback ends the call, timestamps stamped); legacy
  `test_s2s_task_manager.py` pins pass unchanged through the delegator.

## Data model

None. No collections, no migrations.

## Security notes

- Log lines carry identifiers only (verbatim).
- `make sec` clean.

## Test plan

New seam tests + `make check` (s2s/telephone suites exercise neighbors).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Stream-sid wait → `welcome.py` + delegator + tests
  (voice budget 47000→47100 with ratchet-down note).
