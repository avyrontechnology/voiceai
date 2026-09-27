# Spec 0044: hybrid contract matrix (backend)

## Goal

Prove the hybrid agent (`channels: ["voice","chat"]`, one agent, two channels —
the approved product shape) end to end at the HTTP/WS boundary: both legs
serve, each leg denies what it must, and per-task pipeline selection resolves
identically in audit, inference, and dispatch. No new endpoints, no new
models — this spec is executable proof of the Phase A/C contract.

## Non-goals

- UI work (spec 0045). New channels/pipelines. Runtime behavior changes:
  any red test that is not a missing pin is a bug report, not a fix-here.
- Legacy `agent_manager/` edits (forbidden).

## Interface contracts (binding — disjoint file sets per agent)

**Slice A — leg-matrix owner (Agent 1).** Files ONLY:
`voiceai/modules/chat/tests/test_hybrid_channels.py` (new),
`voiceai/modules/voice/tests/test_hybrid_channels.py` (new):
- One hybrid agent fixture (`channels: ["voice","chat"]`, voice task with
  transcriber+synthesizer blocks AND a chat task, LLM-only): POST
  `/chat/{id}` streams SSE 200; voice WS ticket path binds the tenant and
  runs (faked service, real gate).
- Denial pins (same agent family): voice-only agent → chat POST 400 channel
  mismatch (same code as unknown, no oracle); chat-only agent → voice WS
  close with the unknown-agent code; foreign-tenant hybrid → 404 both legs.
- Tenant binding pin: hybrid run on each leg stamps the SAME tenant hex
  (ticket principal + session principal project identically).

**Slice B — pipeline-matrix owner (Agent 2).** Files ONLY:
`voiceai/modules/agents/tests/test_pipeline_matrix.py` (new),
`voiceai/modules/agents/static_methods.py` (only if a gap forces a fix —
  default is pins-only; any edit is a loud deviation):
- `resolve_pipeline_for_task` matrix: explicit asr/s2s/chat win; absent +
  s2s block → s2s; absent otherwise → asr; non-conversation + pipeline →
  reject; multi-task agents resolve PER TASK (mixed pipelines in one agent).
- Audit pins on a hybrid write: both pipeline blocks validated (inactive
  parked, never exempt); chat task with transcriber/synthesizer/s2s →
  problem naming the path; chat task without `llm_agent` → problem.
- PATCH pins: `tasks_patch[].pipeline` flips asr↔s2s↔chat per task;
  `clear` drops to inference; present-null is a no-op.

## Data model

None. No collections, no fields, no migration.

## Security notes

- No-oracle posture pinned per leg (denial codes identical to unknown).
- Session fixation: hybrid session ids are per-agent (confused-deputy guard
  already pinned; matrix asserts cross-agent session 404 on both legs).
- `make sec` clean; no new deps.

## Test plan

Slice A: leg matrix with fakes (factory app + httpx for chat; fake socket
for voice, existing precedents). Slice B: pure matrix + audit pins.
Integrator: full gate.

## Verification

```sh
make check
make sec
```

## Rollout

Tests-only additive change. Rollback is revert. Any red pin that exposes a
real behavior gap becomes a follow-up spec, never a drive-by fix here.

## Burn-down

- [x] Slice A: leg matrix (Agent 1).
- [x] Slice B: pipeline matrix (Agent 2).
- [x] Integrator: gate + report (no commit — gate-held).

## Integration notes (integrator resolutions, all loud)

- **0044-vs-0038 conflict on the chat-leg denial code (kept 0038).** Slice A
  asked wrong-channel denials to carry the unknown-agent code, but spec 0038
  deliberately pins `ChatChannelError` (400 `INVALID_REQUEST`) as distinct
  from the 404 no-oracle path (existing `test_voice_only_agent_post_is_400`
  precedent). Production unchanged; the test pins current reality
  (`test_voice_only_denial_keeps_its_own_code`) with this note. Reconciling
  the posture (oracle analysis + code unification) belongs to a follow-up
  spec, never a drive-by edit. Voice leg already denies with the unknown
  code both ways (`WS_CLOSE_UNKNOWN_AGENT`).
- Slice B needed zero production edits (all 21 pins green as-is); its two
  read-notes accepted: `resolve_pipeline_for_task` is inference-only (schema
  validator owns the reject), and `clear` nulls rather than deletes the key
  (resolve treats both identically).
