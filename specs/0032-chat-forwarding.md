# Spec 0032: browser-leg chat forwarding move (M5 slice 3)

## Goal

Move TaskManager's browser-leg transcript forwarding pair
(`_forward_browser_text`, `_drain_pending_chat_forward`) verbatim into a new
`voice/session/chat.py`, leaving thin delegators. The pair is the chat-panel
send side — its own cohesive surface, forward-placed for the Phase C chat
runtime.

## Non-goals

- Any other TaskManager region (proactive generation, hangup leftovers,
  regen/settle, health reporting, S2S remainder — later M5 slices).
- Signature or semantics changes (verbatim move, quirks preserved: dedupe
  window, 50-entry bound, never-raises send, browser-leg gating).
- Moving `_is_browser_leg` (predicate stays; used in 4+ live sites).

## Interface contracts

- New `voiceai/modules/voice/session/chat.py`: `forward_browser_text(session,
  text, role, asr_turn_id=None)` + `drain_pending_chat_forward(session)`,
  session-first, `otobaai.voice` logger per move discipline.
  `create_ws_data_packet` already bridged (function_runtime adapter).
- `TaskManager` methods become one-line delegators.
- Tests: new `voiceai/modules/voice/tests/session/test_chat.py` drives the
  seam directly (fake session + fake output handler: dedupe, bound,
  non-browser no-op, drain order, send-failure swallow); legacy
  `tests/test_browser_leg_transcripts.py` passes unchanged through the
  delegators (proves the move).

## Data model

None. No collections, no migrations.

## Security notes

- Transcript text flows to the browser panel as before (no new sink, no new
  logging of payloads — identifiers/counters only, existing lines verbatim).
- `make sec` clean.

## Test plan

New seam tests + legacy pin suite green + `make check`.

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Chat forwarding pair → `session/chat.py` + delegators + tests
  (voice budget 46500→46600 with ratchet-down note).
