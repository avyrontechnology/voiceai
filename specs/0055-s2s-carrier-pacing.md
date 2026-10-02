# Spec 0055: S2S carrier-leg paced framing

## Goal

PSTN calls on S2S agents (Gemini Live via Talko/Tata) sound choppy with audible
breaks while the same agent on the browser leg is perfectly smooth. The provider
emits `AudioDelta` chunks irregularly (20–270ms gaps in production logs) and the
engine forwards each chunk as one carrier `media` blob (0.08–0.6s of audio), so
the relay hands Tata burst-gap-burst audio. This spec paces carrier-leg S2S
output: accumulate encoded mu-law into a per-call buffer and emit uniform
120ms frames on a steady tick, absorbing provider delivery jitter before it
reaches the carrier. Browser legs are untouched (the browser buffers itself).

## Non-goals

- No change to the carrier wire protocol: still pre-mark + media + post-mark
  per queued packet, only packet sizes and cadence become uniform. Relay and
  Tata need no changes.
- No change to provider VAD, turn-taking, barge-in accounting, or first-token
  latency. No prompt or model changes (the 2s reply gap is separate tuning).
- No jitter buffer on the inbound (caller-audio) path.
- `freeswitch`/`sip-trunk` legs: pacer activates only when the S2S output
  encoding is mu-law 8k (all mulaw carriers get it uniformly, behavior verified
  by the same tests).

## Interface contracts

Owning module: `voiceai.modules.voice` (session region). Files touched:

- `voiceai/modules/voice/constants.py` — add `S2S_PACER_FRAME_B = 960`
  (120ms @ 8k mu-law), `S2S_PACER_TICK_S = 0.12`, `S2S_PACER_PRIME_B = 1920`
  (~240ms lookahead before the first paced frame).
- `voiceai/modules/voice/session/s2s_runner.py` — per-call pacer buffer
  (`_s2s_pacer_buf`, `_s2s_pacer_primed`, `_s2s_pacer_active`), `_s2s_pacer_loop`
  (tick emitter, carrier legs only), `_s2s_pacer_flush(final)` (full frames on
  tick; remainder + nothing on turn end — the existing sentinel still follows
  in `_s2s_finish_turn`). `AudioDelta` buffers instead of queueing when the
  pacer is active; `_s2s_drop_queued_audio` also clears the buffer (barge-in
  stays instant, no re-prime penalty); `_run_s2s_conversation` starts/cancels
  the pacer task. Bare-harness callers (no `_run_s2s_conversation`) keep the
  legacy direct-queue path via `getattr` fallbacks.
- `voiceai/agent_manager/task_manager.py` — two same-named thin delegators
  (`_s2s_pacer_loop`, `_s2s_pacer_flush`), the file's own B5 pattern.
- `tests/test_s2s_task_manager.py` — new `TestCarrierPacing` class (offline).

Tradeoff (loud): priming adds ~240ms one-time delay to the greeting start and
each tick quantizes output to 120ms. Reply-gap tuning (VAD/prompt) is out of
scope.

## Data model

N/A — no persisted state; buffer lives on the call session and dies with it
(bounded: at most ~2 frames + one in-flight chunk).

## Security notes

No new endpoints, secrets, outbound calls, or PII. Audio stays in-memory on
the session; logging unchanged (no audio bytes logged).

## Test plan

- `TestCarrierPacing`: buffers below prime threshold (queue stays empty);
  tick emits uniform 960B frames leaving remainder; turn end flushes remainder
  before the sentinel; barge-in clears buffer + queue; browser leg still queues
  directly.
- Existing suites must stay green unchanged: `test_s2s_task_manager.py`,
  telephony output/handler tests (wire protocol identical), construction
  characterization.

## Verification

```sh
make check
make sec
```

Spec-specific: `.venv/bin/python -m pytest tests/test_s2s_task_manager.py -q`.

## Rollout

No flag: pacer activates on carrier S2S legs only; browser/turn-based legs
take the legacy path. Rollback = revert this change (single commit). Deploy
requires rebuilding the `voiceai-app` image (running containers keep old code).

## Burn-down

- [ ] Pacer implemented + tests green via `make check`
- [ ] `voiceai-app` image rebuilt and one PSTN call confirms smooth audio
