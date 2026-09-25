# Spec 0027: session webhooks extraction (M5 slice 1)

## Goal

Continue the spec-0004 strangler pattern into M5: move TaskManager's
call-webhook + api-call-ledger cluster (lines ~310–545: header sanitizing,
LLM latency stamping, runtime-args extraction, call-context building,
pre-call webhook firing, api-call detail start/finalize) verbatim into
`voice/session/webhooks.py`, leaving thin delegators. Behavior-identical;
pinned by moved + existing tests.

## Non-goals

- Any other TaskManager region (proactive generation, hangup leftovers,
  regen/settle, health reporting, S2S remainder — later M5 slices).
- Signature or semantics changes (verbatim move; quirks preserved, including
  the fire-and-forget webhook and the `<redacted>` header set).
- Deleting the delegators (TaskManager stays API-stable for legacy callers).

## Interface contracts

- New `voiceai/modules/voice/session/webhooks.py`: module functions taking
  `session` first (duck-typed, following the `HistorySession` Protocol
  precedent in `turn/history_sync.py`), verbatim bodies.
- `TaskManager` methods become one-line delegators (`return await
  _voice_webhooks.fire_pre_call_webhook(self, ...)`), mirroring the
  `sync_history` precedent at task_manager.py:1303.
- Import direction: `task_manager.py` imports the session module (legacy →
  new-arch is outside the §3.1 adapter rule's scope — the rule constrains
  `modules/**` files; precedent: task_manager already imports
  `voiceai.modules.voice.session.composition` per the B13a comment).
- Tests: new `voiceai/modules/voice/tests/session/test_webhooks.py` drives
  the seam directly (fake session); `tests/test_pre_call_webhook.py` keeps
  passing unchanged through the delegators (proves the move).

## Data model

None. No collections, no migrations.

## Security notes

- Header redaction set (`authorization`, `proxy-authorization`, `x-api-key`,
  `api-key`) moves verbatim — no weakening; a test pins the set.
- Webhook firing stays fire-and-forget (no new await points, no timeout
  behavior change).
- `make sec` clean.

## Test plan

New seam tests (sanitize matrix, latency-dict stamping, context fields,
webhook payload substitution, detail start/finalize shapes) + full legacy
`test_pre_call_webhook.py` green + `make check`.

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Webhook + api-call cluster → `session/webhooks.py` + delegators + tests
  (bridge export for the SSRF guard; legacy patch targets retargeted to the
  live namespace; voice budget 46000→46500 with a ratchet-down note).
- Later M5 slices (not this spec): proactive generation, hangup leftovers,
  regen/settle, health reporting, S2S remainder.
