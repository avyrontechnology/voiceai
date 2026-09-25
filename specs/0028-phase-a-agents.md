# Spec 0028: Phase A — channels, pipeline selector, PATCH (agents restructure)

## Goal

Agents gain first-class channel architecture and switchable pipelines:
`channels` (voice-only allowlist in Phase A), per-task `pipeline` selector
(`asr|s2s`, inferred when absent), and `PATCH /agent/{id}` deep-merge
updates — all strict-schema, catalog-validated, tenant-isolated, with zero
DB migration and byte-identical behavior for existing rows.

## Non-goals

- Chat runtime (channel values beyond voice reject until Phase C).
- Tools module, webhooks sharing, identity/org-teams, inbound routing,
  provider webhook signatures, realtime hot-path changes.
- DB migration/backfill, new collections/indexes, admin catalog CRUD, BYOK.

## Decisions (approved)

1. Non-voice channels reject 400 until Phase C (no dormant data).
2. Both pipeline blocks validate clean on every write (strict, no lenient
   inactive).
3. PATCH null = no-op; clearing uses explicit clear ops.

## Interface contracts

**Slice 1 — schema + validation.**
- `models/channel.py`: `Channel = Literal["voice", "chat"]`,
  `Pipeline = Literal["asr", "s2s"]`; constants `CHANNEL_VOICE`,
  `CHANNEL_CHAT`, `PIPELINE_ASR`, `PIPELINE_S2S`.
- `AgentModel.channels: list[Channel]`, min 1, unique, default `["voice"]`;
  service allowlist `{voice}` (400 + valid values otherwise).
- `Task.pipeline: Pipeline | None`; `None` infers legacy (`s2s` block +
  conversation → s2s else asr); set on non-conversation task → 400.
- `audit_provider_config(..., active_pipelines: dict[int, str] | None)`:
  both blocks catalog-checked regardless (strict); unknown channel at
  runtime rejected by service allowlist.
- Inference parity table (both→s2s, asr-only→asr, neither→asr, s2s on
  non-conversation→asr).

**Slice 2 — PATCH endpoint.**
- `PATCH /agent/{id}` (`AGENT_BY_ID_PATH` value reused): strict partials
  (`extra="forbid"`), recursive dict merge, wholesale list replace,
  present-null no-op, explicit clear ops, unknown paths 422.
- Load (scoped) → merge (pure) → full `AgentModel` validate → full catalog
  walk → conditional extraction regen → atomic save. Response
  `{"agent_id", "state": "updated"}` (PUT parity). Missing id follows the
  swallowed-404 quirk (documented debt, not diverged).

**Slice 3 — engine routing.**
- `resolve_pipeline(task)` pure in `voice/session/config.py`; predicate
  swaps in composition legs, greeting/DTMF/backchannel, IO defaulting
  (bodies verbatim); voice WS channel gate → 4404. Legacy trees frozen.

## Data model

None. Storage stays opaque dicts; new keys materialize via defaults.

## Security notes

- Tenant isolation via scoped ports on every read/write (cross-tenant PATCH
  sees nothing — pinned by test).
- 400s carry valid values only, never secrets. `make sec` clean.

## Test plan

Slice 1: defaults/strict/matrix unit tests + parity table. Slice 2: merge
semantics, atomicity, 400 shapes, isolation pins, controller via factory.
Slice 3: parity suite + selector matrix + channel-gate test.

## Verification

```sh
make check
make sec
```

## Rollout

Additive fields + endpoint; single merge per slice. Rollback is revert.

## Burn-down

- [x] Slice 1: schema + validation (channels, pipeline selector, inference
  parity, both-blocks-strict audit, service allowlist).
- [x] Slice 2: PATCH endpoint (strict partials, deep-merge, atomic save,
  conditional extraction regen, swallowed-404 parity).
- [x] Slice 3: engine routing (`resolve_pipeline` on the surface,
  `CallConfig.is_s2s` field, predicate swaps, voice channel gate).
