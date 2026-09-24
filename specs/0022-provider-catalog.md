# Spec 0022: provider catalog for the agent builder (M-catalog)

## Goal

Tenants build agents from dropdowns, never from memory: a curated Provider
Catalog serves provider/model/voice/language options per modality
(`asr|tts|s2s|llm`), and agent create/update validates against it — so an
invalid provider string fails at the builder with a 400, never at 2am in the
realtime path.

## Non-goals

- Provider-API sync jobs; admin catalog CRUD (curation stays in code review).
- On-demand synthesis preview (static sample URLs only).
- BYOK tenant keys (`requires_tenant_key` reserved, unenforced).
- Realtime hot-path changes (validated-at-write is the whole fix).
- Auto-fixing existing agents (report + grandfather, validate on next update).

## Decisions (approved)

1. Code-reviewed seed file (versioned, no new infra).
2. Static curated voice sample URLs.
3. Existing invalid rows: report-only audit; next update must validate clean.
4. New `modules/catalog` module (builder UI + agent validation + future
   realtime reads = three consumers).
5. Strict language validation (finite BCP-47 lists).

## Interface contracts

**Slice 1 — catalog module.**
- `Collections.PROVIDER_CATALOG = "provider_catalog"`; rows are system-tenant
  (`SYSTEM_TENANT_ID`), read by every tenant, written only by seeding.
- Models (`models/` package if >3 models per rule 1a): `CatalogEntry`
  (`catalog_id`, `modality`, `provider`, `model`, `languages[]`,
  `voices[]` of `CatalogVoice{name, gender?, language, sample_url?}`,
  `deprecated=false`, `requires_tenant_key=false`, `catalog_version`),
  inheriting `BaseFields` (+ `tenant_id="system"` stamped at seed).
- Seed: `seed.py` data module (reviewable tables) + `seed_catalog(store)`
  loader, idempotent by `catalog_id` (insert-or-replace, never duplicate).
  Modalities land asr+tts first, s2s+llm in the same slice if the data is at
  hand, else a fast follow commit in-slice.
- Read endpoints (no auth scope — system rows are public facts, and
  provider/model names are public knowledge; deprecation filtered server-side):
  `GET /api/v1/catalog/{modalities,providers,models,voices}` — one shaped
  endpoint per dropdown need, deprecation filtered server-side:
  - `GET /catalog/modalities` → the four modalities.
  - `GET /catalog/providers?modality=tts` → providers with model counts.
  - `GET /catalog/models?modality=tts&provider=elevenlabs` → models + languages.
  - `GET /catalog/voices?provider=&model=` → voices + sample URLs.
- Service: pure lookups over the repository; no HTTP types; errors are
  `CatalogError` 404s on unknown modality/provider (never empty-200 — a
  typo'd query must fail, not render an empty dropdown).

**Slice 2 — write-time validation + audit script.**
- `agents` service validates `config` provider/model/language tuples
  against the catalog on create/update (via the catalog service through the
  container — cross-module service use, matrix-sanctioned): unknown →
  `AgentConfigInvalidError` 400 carrying the valid values (did-you-mean).
- Two-tier strictness so the catalog can never false-reject (spec 0022
  amendment): `models_open` entries (LiteLLM-routed LLMs, Azure deployments,
  Deepgram, self-hosted) suggest one model in dropdowns but accept any
  non-empty string; closed entries must match exactly. Voice validation lands
  in slice 3 with the voice curation (`voices_open` for open marketplaces).
- Grandfather: existing rows untouched; their next update validates.
- `voiceai/tooling/validate_agents.py`: report-only audit (lists invalid
  rows; exit non-zero when any found, for CI).

**Slice 3 — samples + UI sync.**
- Seed backfill of voice sample URLs + genders/languages; UI sync spec for
  the builder dropdown bindings (separate spec file, UI track).

## Data model

`provider_catalog` collection (new `Collections` member). System-tenant rows;
`catalog_id` natural key (`{modality}:{provider}:{model}`); `catalog_version`
per seed revision. No migration: new collection, seeded at deploy.

## Security notes

- Read endpoints: authenticated, no per-row auth (system rows are public to
  tenants by design); sample URLs are static CDN links (SSRF N/A — never
  fetched server-side).
- Validation errors echo the *valid* values, never secrets; provider API keys
  never appear in catalog rows.
- `make sec` scope unchanged (no outbound calls, no eval, no shell).

## Test plan

Slice 1: seed idempotency (double-seed, no dupes); endpoint shape tests
through the app factory; unknown modality/provider → 404; deprecated hidden
from dropdown payloads but resolvable by id. Slice 2: create/update with
typo'd provider/voice → 400 with valid values; grandfather row readable,
update blocked until fixed; audit script exit codes. Slice 3: every voice
has a sample URL + language (seed completeness test).

## Verification

```sh
make check
make sec
```

## Rollout

New collection + additive endpoints + additive validation (create/update only
— reads never validate). Rollback is revert. The seed loader runs at deploy;
re-runs are idempotent.

## Burn-down

- [x] Slice 1: catalog module + seed (58 rows, all registry keys censused) + read endpoints.
- [x] Slice 2: write-time validation (provider/model/language; synthesizer
  provider_config shapes deferred to slice 3 with voices) + report-only audit.
- [ ] Slice 3: samples + UI sync spec.
