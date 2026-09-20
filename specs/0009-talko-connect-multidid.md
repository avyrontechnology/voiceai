# Spec 0009 — Talko connect flow + multi-DID per partner

- **Status:** done
- **Branch:** `revamp/arch` (base: `revamp/arch`)
- **Owner:** arch agent
- **Depends on:** spec 0008 (place-call port)

## Goal

Replace the hand-typed partner form with a connect flow: the operator pastes one
Talko partner API key, the backend validates it and fetches that partner's DIDs
live from talko-service, and connecting stores the record — no hand-typed DIDs,
base URLs, or vendor ids. Partners then carry multiple DIDs with one default,
and dial dialogs offer a DID picker instead of free-text fields. Verified live
against local talko-service (prod Mongo) before speccing.

## Non-goals

- Changing talko-service (its `GET /dids/list-dids` already serves; no new endpoints there).
- Touching the trunk server or the Tata protocol.
- Migrating existing records automatically (one-shot backfill below, operator-run).
- UI work beyond the partners manager + the two dial dialogs (no new pages).

## Design

`GET {talko-service}/dids/list-dids` is permission-checked and partner-scoped:
a valid key returns 200 with that partner's DIDs (`did_number`, `status`, ...);
a bad key returns 401. That single call is both key validation and DID fetch —
no separate validate step, no new talko-service surface.

Rule 1 placement (new files only where the feature needs them):
- `modules/voice/models.py`: `TalkoPartnerConfig.dids: list[str]` (digits-normalized
  on write) + `TalkoPartnerPreview` (partner_id | None, dids, display guesses —
  never persisted, never carries the key back out); views expose `dids`.
- `modules/voice/constants.py`: fetch timeout, preview route path, detail keys.
- Error mapping (no new classes): bad key → `PlaceCallError` (400,
  "invalid partner key"); unreachable service → common
  `DependencyUnavailableError` (503, retryable).
- `modules/voice/adapters/outbound.py`: `fetch_partner_dids(*, talko_api_key,
  talko_api_base_url) -> PartnerPreview` (httpx GET, bounded timeout; bridge 1 —
  the only file touching talko-service HTTP besides the trunk).
- `modules/voice/service.py`: `preview_partner(talko_api_key)` (no persistence);
  `connect_partner(talko_api_key, display_name?, partner_id?)` = preview + upsert
  (first DID becomes default when unset); `refresh_partner_dids(partner_id)` =
  re-fetch with the STORED key and merge (additive: never drops the default).
  `place_call`: `from_number`, when given with a partner that has non-empty
  `dids`, must be a member (digits-compared) else 400; records without `dids`
  keep legacy free-form behavior (backward compat).
- `modules/voice/controller.py`: `POST /talko/partners/preview` (key in body,
  never logged, never persisted); keep CRUD, extend views with `dids`.
- `TalkoPartnerView.dids: list[str]`, `default_did` stays the selected one.
- Base URL resolution for fetch: `Environment` gains `talko_service_base_url`
  (Rule 4 — no `os.getenv` outside `core/environment.py`); container threads it
  into the service; per-request override still wins, then partner record, then env.
- Backfill: existing DB records keep working (`dids == []` ⇒ free-form DID as
  today); a `refresh` call fills them in. No migration script.

## Interface contracts

```python
# models
class TalkoPartnerPreview(BaseModel):
    partner_id: str | None  # derived from first DID; None when empty
    dids: list[str]  # digits-normalized, status Mapped first
    display_name: str = ""  # nickname guess, operator-editable

# service
async def VoiceCallService.preview_partner(self, *, talko_api_key: str) -> TalkoPartnerPreview
async def VoiceCallService.connect_partner(self, *, talko_api_key: str, display_name: str = "", partner_id: str | None = None) -> TalkoPartnerView
async def VoiceCallService.refresh_partner_dids(self, *, partner_id: str) -> TalkoPartnerView

# routes (dual-served bare + /api/v1, spec-0007 pattern)
POST /talko/partners/preview -> 200 TalkoPartnerPreview | 400 invalid key | 503 service down
POST /talko/partners/connect -> 201 TalkoPartnerView (replaces hand-typed create in UI; create stays for API compat)
POST /talko/partners/{id}/refresh -> 200 TalkoPartnerView | 404
```

Credential precedence unchanged (spec 0008). Key-in-body endpoints: the key is
used once for the fetch and (on connect) stored; preview responses never echo it.

## Data model

- `TalkoPartnerConfig.dids: list[str]` (new field, default `[]`); `default_did`
  should be a member when `dids` is non-empty (enforced at connect/refresh, not
  by update — operators may stage either order).
- Same `Collections.TALKO_PARTNERS` collection; records are documents, so the
  field is additive with no migration.

## Security notes

- The partner key crosses into the service only in POST bodies (TLS-terminated
  transport assumed as today); never logged (redact before any mapping dump),
  never returned (preview/view are key-free by construction — test asserts the
  key string absent from every response body).
- Fetch is a server-side GET with bounded timeout; the base URL comes from
  `Environment` or the stored record — never from the request (SSRF: no
  caller-controlled hosts). Redirects not followed blindly (httpx defaults).
- Scope gates unchanged (`platform:write` connect/refresh, `platform:read` preview?
  Preview validates a third-party secret — gate it `platform:write` too, since a
  successful preview is one click from storing it).
- Abuse: preview is unauthenticated-secret-oracle adjacent (key validity probe);
  bound by the existing auth gate + no key-material reflection. Note the residual
  and move on (talko-service itself rate-limits keys).

## Test plan

- `tests/arch/modules/voice/test_partner_connect.py` (new): preview maps DID
  payloads (digits normalization, Mapped-first ordering, partner derivation,
  empty → partner_id None); key-invalid (401) → 400 client error; transport
  failure → dependency error; connect upserts with first-DID default; refresh
  merges additively and keeps default; place-call rejects foreign DID (400)
  but allows free-form when `dids == []`; key string absent from all bodies.
  Outbound port fakes the fetch (no network).
- Controller through the factory: preview/connect/refresh roundtrip + 404/400 paths.
- Offline only.

## Verification

- `make check`, `make sec`, `make cov` (≥85% on new code).

## Rollout

- Additive: existing records/flows untouched (`dids == []` preserves old behavior).
- UI (separate change): connect card replaces the big form; dialogs gain DID
  dropdowns; manual DID entry stays as fallback.
- Revert: drop routes/methods; records with `dids` are inert to old code.
- Follow-ups (not this spec): batches port, numbers port, stable tunnel/DID docs.
