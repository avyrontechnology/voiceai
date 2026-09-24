# Spec 0021: channels own their gates (M2)

## Goal

Every realtime path authenticates its own callers by its own credential type
and binds its own tenant: the WS chat handler redeems single-use tickets and
serves only the ticket holder's tenant; outbound dials resolve the agent
through the scoped port; execution records carry the call's tenant; channel
lifecycle events give one queryable story per call.

## Non-goals

- ULIDs: deferred. Prefixed ids (`usr_…`, `inv_…`) isolate fine; time-ordering
  is not worth touching every model, fixture, and legacy shim.
- Provider inbound webhooks (Twilio/Talko signatures): no such code exists;
  the first inbound provider route carries its own spec.
- Quickstart's legacy WS path and the legacy `engine_hook` execution record:
  cutover-owned, untouched.
- `run_call` engine signature: stays engine-opaque (no HTTP types in the
  service layer); the tenant travels ambient + container scoping.

## Decisions (approved)

1. WS credential is the single-use `?ticket=` redeemed at connect
   (`auth.mint/redeem_ticket` exist for exactly this; 60s TTL, calls-scoped).
2. Dedicated close codes per denial (clients branch on codes, never messages).
3. Denied attempts log at WARNING with identifiers (agent id, reason) — never
   the token. Abuse visibility, PII-safe.

## Interface contracts

**Slice 1 — WS gate.**
- `voice/constants.py`: `WS_TICKET_PARAM = "ticket"`, `WS_CLOSE_DENIED = 4401`
  (mirrors HTTP 401, same 44xx app-code scheme as 4403/4404).
- `voice/controller.py::voice_chat`: accept → dark-check (4403, unchanged) →
  redeem `?ticket=` via container `AuthService` (tenant-free construction, so
  it stays a `Depends`) → deny closes 4401 + WARNING log → bind
  `TenantContext(org of principal, request_id=new_id("ws"), scopes)` →
  resolve voice service + definitions **from `websocket.app.state.container`
  inside the binding** (Depends-time construction would read an unbound
  tenant — BaseHTTPMiddleware never runs on websockets) → scoped agent load
  (unknown-or-foreign → 4404, no oracle) → run inside the binding.
- Principal additionally gated on `calls:write` at redeem (role may have
  changed since the 60s-TTL mint).
- New tripwire `tests/arch/test_channel_gates.py`: files under the new-arch
  roots mentioning `ticket` must equal `TICKET_TOUCHED` (auth + voice
  controller + allowlisted tests) — tickets never sprout a third home.

**Slice 2 — outbound ownership + execution tenant.**
- `place_call` resolves `payload.agent_id` through the scoped definitions
  port; unknown-or-foreign agent raises the same error as the unknown-partner
  path (no oracle). No signature changes; ambient binding covers the
  `PlacedCall` record (asserted tenant-stamped in tests).

**Slice 3 — channel lifecycle events.**
- Adapters emit connect/deny/dial/record events as tenant-stamped `AuthEvent`
  rows through `AuthService.audit()` (new `type` strings, no new collection —
  a `ChannelEvent` collection is M-later if volume demands).

## Data model

None. No collections, no migrations. `PlacedCall` rows were censused as
default-stamped in the M1b backfill.

## Security notes

- Tickets are single-use by construction (redeem deletes first, then
  validates — a racing second redeem finds nothing).
- Close codes carry no reason text; the WARNING log carries identifiers only.
- Scope is re-checked at redeem, not just at mint.
- `request.state.principal` is NOT used on the WS path (no middleware ran);
  the handler stashes nothing — the principal lives in handler locals.
- Timeouts: redeem is one indexed store read (same bound as any auth resolve).

## Test plan

Slice 1: real app + `starlette.testclient` WS client — accepted ticket runs
with the ticket holder's tenant (fake service captures `current_tenant()`);
missing/invalid/used-twice/scope-less tickets → 4401; unknown + cross-tenant
agent → 4404; dark flag → 4403; deny WARNING logged with agent id, never the
token. Slice 2: cross-tenant agent dial denied; execution row stamped. Slice
3: event rows exist per lifecycle transition, tenant-stamped.

## Verification

```sh
make check
make sec
```

## Rollout

WS stays dark until the cutover flag flips (unchanged); the gate ships inert
under it. No migrations, no flags beyond the existing one. Rollback is revert.

## Burn-down

- [x] Slice 1: WS ticket gate + tenant binding.
- [x] Slice 2: place_call agent ownership + execution tenant proof.
- [x] Slice 3: channel lifecycle events (`ws_connect`/`ws_denied` in the WS
  handler, `call_placed` in the place-call handler, `call_recorded` in the WS
  run finally) — all through `AuthService.audit()`, tenant-stamped, no new
  collection. WS-run `PlacedCall` rows still flow through the legacy
  `engine_hook` till cutover (documented, not re-plumbed here).
