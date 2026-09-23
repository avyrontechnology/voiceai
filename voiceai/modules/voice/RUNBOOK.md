# voice RUNBOOK

Owner: `squad-voice` (`#squad-voice`).

## Alerts

- `OUTPUT_SEND_TIMEOUT` / dead-socket drops — carrier leg died; the output latch
  mutes the rest of the call by design (check `stream_sid` + mark ack stats).
- `transcriber_connection_closed` bursts — provider-side; pool reconnects (cap 5)
  then degrades. Check provider status before restarting.
- `task_output` PII note — execution logging records last-message payloads at INFO
  (known debt, TODO in service); keep these logs out of broad shares.
- Rising `WAIT`-gate spins — LID playback gate or grace delay holding audio;
  check language-switch tunables, not the loop.

## Scaling

Single stateful process today: one `TaskManager` per call, `TaskRegistry`-tracked
tasks cancelled on shutdown. Per-call ledgers (`request_logs`, marks) are the
memory risk on long calls — the spec bounds them. Do not replicate voice without
the Redis-backed `SessionStore` cutover (separate spec).

## Rollback

`VOICE_WS_ENABLED=0` darkens the WS route instantly with quickstart unaffected.
Execution/partner rows are soft-state; no migrations in normal operation.
