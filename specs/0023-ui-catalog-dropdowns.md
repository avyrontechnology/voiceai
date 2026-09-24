# Spec 0023: builder dropdowns bound to the provider catalog (UI track)

## Goal

The agent builder offers provider/model/voice/language as cascading dropdowns
fed by `GET /api/v1/catalog/*` (spec 0022): tenants can no longer type a
provider string, and a 400 from write-time validation renders the valid values
instead of a dead form.

## Non-goals

- Builder layout redesign (bind existing selects, don't restyle).
- On-demand synthesis preview (static `sample_url` playback only).
- Admin catalog CRUD; BYOK key inputs.

## Backend contract (frozen by spec 0022, slice 1)

- `GET /api/v1/catalog/modalities` → `{ok, data: ["asr","tts","s2s","llm"]}`.
- `GET /api/v1/catalog/providers?modality=` → `{ok, data:
  [{provider, models, deprecated}]}` (404 on typo'd modality).
- `GET /api/v1/catalog/models?modality=&provider=` → paginated envelope,
  items `{catalog_id, modality, provider, model, languages, models_open,
  deprecated}` (deprecated hidden; 404 on typo'd provider with valid list).
- `GET /api/v1/catalog/voices?provider=&model=` → `{ok, data:
  [{name, gender, language, sample_url}]}`.
- Agent create/update 400s carry `error.details.problems[]` (paths + valid
  values) — the form renders these verbatim beside the offending select.

## UI contracts

- New `src/services/platform/catalog.ts`: `catalogKeys` + four hooks
  (`useCatalogModalities`, `useCatalogProviders(modality)`,
  `useCatalogModels(modality, provider)`, `useCatalogVoices(provider, model)`)
  following the `voices.ts` react-query pattern (queryKey invalidation on
  nothing — catalog data is static per deploy; `staleTime: Infinity`).
- New zod schemas in `src/lib/schemas/catalog.ts` mirroring the backend
  shapes (round-trip test against example payloads, talko parity style).
- Builder bindings (existing selects in the agent form):
  - Transcriber: provider select (asr) → model select (exact list, or free
    text when `models_open`) → language select (suggested list + free
    BCP-47 input).
  - Synthesizer: provider select (tts) → model → voice select (names from
    the voices hook; free text only when the row has no curated voices —
    mirror the backend gradual rule) + sample playback button when
    `sample_url` is present (`<audio>` element, no custom player).
  - LLM: provider select → model (free text when `models_open`).
  - S2S: provider select → model select (closed sets).
  - Deprecated providers/models render with a badge, selectable only when
    editing an agent that already uses them (grandfather UX).
- 400 handling: `problems[]` entries render beside their select (path →
  field mapping: `tasks[N].transcriber` etc.); the form never clears valid
  inputs on a validation failure.

## Test plan (UI track, jest)

Hook tests with mocked `apiClient` (shapes parse; 404 propagates); schema
round-trips; builder interaction test (select provider → model options
populate; deprecation badge; sample button renders only with URL).

## Verification

```sh
cd ../voiceai-ui && npm test -- catalog && npm run lint
```

## Rollout

UI-only, additive. Builder works unmodified against old backends (hooks
degrade to current free-text inputs when the catalog endpoints 404 — feature
detect, don't version-gate).
