# voice

Realtime call runtime: session composition, per-turn STT→LLM→TTS pipeline,
interruption/barge-in policy, language switching, lifecycle/teardown, place-call
and Talko partner management.

Owned by `squad-voice`. Biggest module by design — split by subpackage, never by
copying. See `CONTRACT.md` for the seam map and `RUNBOOK.md` for ops.
