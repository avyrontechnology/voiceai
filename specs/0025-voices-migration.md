# Spec 0025: custom voice library migration (M-voices)

## Goal

Serve the UI's per-agent custom voice library from the new app: `GET
/api/v1/voices?agent_id=`, `POST /api/v1/voices`, `DELETE
/api/v1/voices/{voice_id}` — byte-identical wire shapes to the legacy
`platform/router.py::voices_router`, tenant-scoped, agent-ownership-checked.
The UI changes nothing (same paths, same payloads).

## Non-goals

- Audio upload/cloning pipelines (the legacy surface stores records only —
  same here).
- Migrating any other platform router (each gets its own spec).
- Changing the UI (verified compatible, untouched).

## Root cause (prod 404)

Production runs `voiceai.app` (pure new-arch). `voices_router` lives in the
legacy platform router, never mounted there — a strangler gap, not a
regression. The new app answers 404 (enveloped) where the UI manages voices.

## Interface contracts

- New `modules/voices`: `VoiceRecord(BaseFields)` (same fields as legacy
  `VoiceEntry` + `tenant_id`; `id` pinned to `voice_id`), `voices`
  collection, `VoicesRepository` over scoped `BaseRepository`, `VoicesService`
  (create/list/delete), controller with the three legacy routes and legacy
  gates (`platform:read` for list, `platform:write` for create/delete via the
  voice-controller `_require_scope` precedent).
- Ownership: `create_voice` with an `agent_id` resolves it through the scoped
  definitions port (unknown-or-foreign → 404, the place-call precedent);
  agent-less rows (`agent_id=None`) are allowed (library-level voices).
  `list_voices(agent_id)` returns only the caller's tenant rows (scoped repo
  does this structurally); `delete_voice` on a foreign row reads as missing.
- Validation: `provider` must resolve in the catalog (strict — unknown
  providers fail with valid values); `language` BCP-47 when present. Voice
  NAME is free (custom/cloned voices are the point — names are user data).
- Wire shapes: identical JSON to legacy (`voice_id`, `agent_id`, `name`,
  `provider`, `provider_voice_id`, `source`, `language`, `created_at`).
- Backfill: legacy `voices` rows stamp `default` tenant (add to
  `DEFAULT_STAMPED_COLLECTIONS` + census).

## Data model

`voices` collection (new `Collections` member). Tenant-stamped rows; `id` =
`voice_id`. No migration (new collection; legacy rows backfilled per above).

## Security notes

- Scope gates preserved (`platform:read/write`); tenant isolation structural
  via scoped repos; agent ownership checked (no cross-tenant voice attach).
- `make sec` clean (no new outbound/shell/eval).

## Test plan

Unit (service over memory fakes: CRUD, cross-tenant invisibility, foreign
agent attach denied, provider validation); controller through the factory
(legacy UI call sequence: create → list?agent_id= → delete; shapes asserted
key-for-key against the legacy models); backfill census extended.

## Verification

```sh
make check
make sec
```

## Rollout

Additive module + registry entry. Rollback is revert. Legacy router untouched
until its own retirement spec.

## Burn-down

- [x] Voices module + wiring + registry (legacy-identical wire shapes).
- [x] Backfill rule (`voices` → default-stamped) + UI-sequence verification test.
