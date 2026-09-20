# Spec 0008 — Outbound place-call in the voice module

- **Status:** done
- **Branch:** `revamp/arch` (base: `revamp/arch`)
- **Owner:** arch agent
- **Depends on:** spec 0004 (voice module), spec 0007 (dual-serve parity)

## Goal

Port the single-call outbound dial (`POST /calls/place`, previously built on `master` against `voiceai/platform/router.py`) into the new architecture as a voice-module capability, so the existing UI (Place Call dialog + Talko partners manager, already on `main`, unchanged) works against `revamp/arch` with byte-identical request/response contracts. No new legacy code; legacy dial engines are reached only through `voice/adapters/` bridges.

## Non-goals

- Batches, campaigns, batch retry/stop, numbers/inbound mapping (stay legacy; separate specs).
- Changing the Talko trunk server (`local_setup/telephony_server/talko_api_server.py`) or the Tata/talko-service protocol.
- S2S/hydration/resample work from the master line (out of scope for this port).
- UI changes (the `main`-branch UI already speaks this contract).
- Per-partner trunk base-URL routing: `TalkoPartnerConfig.talko_api_base_url` is
  stored and returned, but the legacy runner on this branch takes explicit
  key/DID only, so the adapter does not forward it (no legacy edits per the
  strangler rule). Activates at the trunk endgame with zero contract change.

## Design

Voice module extension, Rule 1 file semantics (create only what the feature needs; no placeholders):
- `modules/voice/models.py`: `PlaceCallRequest`, `PlacedCall` (execution view), `TalkoPartnerConfig`, `TalkoPartnerView`, `CreateTalkoPartnerRequest`, `UpdateTalkoPartnerRequest` (pydantic only; persisted ones inherit `database.base.BaseFields`).
- `modules/voice/constants.py`: provider literals (`simulated`/`talko`), route paths, digit bounds, error detail keys. No literals elsewhere.
- `modules/voice/errors.py`: `PlaceCallError(VoiceError)` + `UnknownTalkoPartnerError(PlaceCallError)` (fail-closed unknown partner). Raise these across layer boundaries, never bare `ValueError`.
- `modules/voice/repository.py` (new): `PlaceCallRepository` protocol + implementation over `database` `BaseRepository` for `Collections.EXECUTIONS` and new `Collections.TALKO_PARTNERS`; secret-free views at the boundary (key never leaves the repo except toward the trunk call).
- `modules/voice/service.py`: `VoiceCallService.place_call(...)` orchestration (validate → resolve partner → dial → persist → return view) plus partner CRUD orchestration. No HTTP types in signatures.
- `modules/voice/controller.py`: thin `POST /calls/place` + `/talko/partners` CRUD handlers (≤30 lines each): parse pydantic input, resolve service from container via `@inject`/`Provide`, wrap output with `common.responses`.
- `modules/voice/adapters/outbound.py` (new, tagged with the retiring migration step): the ONLY new-arch files importing legacy `voiceai.platform.talko_dialer` / `voiceai.platform.simulation` (bridge rule §3.1.1).
- `database/constants.py`: add `Collections.TALKO_PARTNERS` (Rule 5 — no literal collection names).
- `core/container.py`: wire outbound ports + repositories into `VoiceAIContainer`.
- Mounting: voice router carries the routes (→ `/api/v1/...` via app factory) AND quickstart dual-serve twins (bare `/calls/place`, `/talko/partners*`, spec-0007 pattern) so the unchanged UI keeps working.

## Interface contracts

```python
# models
class PlaceCallRequest(BaseModel):
    agent_id: str  # min_length=1
    to_number: str  # min_length=1
    from_number: str | None = None
    variables: dict[str, Any] = {}
    provider: Literal["simulated", "talko"] = "simulated"
    partner_id: str | None = None
    talko_api_key: str | None = None  # never persisted
    delay_scale: float = 0.5  # ge=0; simulated only

class TalkoPartnerConfig(BaseFields):
    partner_id: str  # natural key
    display_name: str = ""
    talko_api_base_url: str | None = None
    talko_api_key: str  # secret: repo never returns it
    default_did: str | None = None
    vendor_config_id: str | None = None

# service (business logic only; store/clients via constructor)
async def VoiceCallService.place_call(self, *, payload: PlaceCallRequest) -> PlacedCall
async def VoiceCallService.create_partner / get_partner / list_partners / update_partner / delete_partner ...

# routes (responses via common.responses envelopes)
POST /calls/place -> 202 PlacedCall
POST /talko/partners -> 201 TalkoPartnerView (key masked: key_configured + key_hint only)
GET /talko/partners -> TalkoPartnerListResponse
GET /talko/partners/{id} -> TalkoPartnerView | 404
PUT /talko/partners/{id} -> TalkoPartnerView | 404 (omitted/empty key keeps stored)
DELETE /talko/partners/{id} -> 404 when missing
```

Credential precedence per field (frozen from master): explicit per-request > partner DB record > trunk env default. Unknown `partner_id` → 400 (`UnknownTalkoPartnerError`, fail closed). Recipient rule: 10–15 digits; `91…` numbers must be 91 + 10 digits (else 400 — Tata rejects them opaquely).

## Data model

- `TalkoPartnerConfig(BaseFields)` in `Collections.TALKO_PARTNERS` (new enum member). DIDs normalized to digits on write.
- Executions persist via `Collections.EXECUTIONS` (existing member) as module views; trunk-dialed rows stay `IN_PROGRESS` (outcome lives in Tata CDR — preserved quirk).

## Security notes

- Boundary: all bodies are pydantic models; acting user from auth context (follow surrounding controller auth patterns; service decides authorization).
- Error opacity: `VoiceError` subclasses render as envelopes; `str(exc)`/tracebacks never reach clients; trunk HTML outage pages are summarized, never pasted.
- Secrets: partner keys live only in DB + per-request trunk body; GET/PUT responses mask (`key_hint` last-4); logs via `otobaai.voice` with `redact_secrets`; no key in `variables` echo.
- SSRF/timeouts: trunk URL resolution stays in the trunk server; every outbound call bounded (follow existing timeout constants).
- Abuse: single-call endpoint inherits the platform rate-limit posture (no new bulk path — batches stay legacy).

## Test plan

- `tests/arch/modules/voice/test_place_call.py` (new): service unit with DI fakes (simulated inline completes; talko uses fake trunk via adapter seam; unknown partner 400s; short/truncated number 400s; explicit-beats-record precedence); repository unit (CRUD + key masking at boundary); controller through the real app factory (202 + envelope; 422 on missing fields; 404/409 partner paths).
- Layer-contract test must stay green (no cross-module internal imports; legacy imports only under `adapters/`).
- Offline only, no network.

## Verification

- `make check` (ruff + strict arch lint + mypy + `pytest tests/arch`) green.
- `make sec` (bandit medium+/high on new packages) clean.
- `make cov` ≥ 85% on new code.

## Rollout

- Additive only: no legacy file is modified or deleted; legacy `/calls/simulate` untouched.
- UI needs no changes (contract parity with master: same paths, same shapes).
- Revert: drop the new routes/wiring; trunk + legacy platform keep serving as before.
- Follow-up specs (not this one): batches port, numbers/inbound port, S2S/hydration line.
