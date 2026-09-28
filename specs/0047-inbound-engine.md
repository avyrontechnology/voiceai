# Spec 0047: inbound engine — lookup, carrier webhook, screening (backend)

## Goal

Make the persisted inbound surface real: carrier calls to an assigned number
reach the right agent, with greeting override, blocklist/spam screening, and
caller-match context — end to end, tenant-safe. Today `/inbound` +
`/phone-numbers` are write-only (zero runtime readers) and no ingress path
exists at all (telephony servers are outbound-only). This spec builds the
missing ingress: number→agent lookup, a Twilio inbound webhook (first
carrier; Plivo/Talko are a follow-up, never this slice), and the screening
pipeline, with every decision recorded on the call record.

## Non-goals

- Plivo/Talko inbound webhooks (follow-up spec; the lookup + screening core
  is carrier-agnostic by construction).
- Business hours / holidays / IVR (no fields, no engine — explicitly
  deferred again, not invented here).
- Legacy `agent_manager/` edits (forbidden); legacy `platform/` READS are
  allowed (assignment rows live there until migrated — read-only, no edits
  to `platform/router.py` or `platform/models.py`).
- UI implementation (UI track binds afterwards; record fields noted below).

## Decisions (approved)

1. **Lookup is tenant-safe by construction, not by caller auth** (carrier
   webhooks are unauthenticated HTTP): a called number maps to at most one
   agent row; the agent's tenant binds the call. Unknown/unassigned numbers
   answer identically (reject) — no cross-tenant oracle via dialing.
2. **Twilio first, behind a carrier seam**: webhook parsing is Twilio-shaped;
   the lookup + screening core takes normalized `(called, caller)` and never
   sees carrier specifics, so Plivo/Talko shims plug in later untouched.
3. **Screening order (fixed):** blocklist → spam protection → caller-match
   enrichment → greeting resolution. Each step is pure, tested, and recorded.
4. **Greeting precedence (documented):** `inbound.greeting` set → wins over
   `agent_welcome_message`; unset → welcome message (today's behavior,
   unchanged). No merge, no fallback chain beyond this rule.
5. **Every decision lands on the call record** (`inbound_screening:
   {decision, reason}` additive field; screening runs pre-answer so the
   record exists even for rejected calls where the platform persists one).
6. **Webhook authenticity**: Twilio `X-Twilio-Signature` validation against
   the account auth token (env, never logged); failures answer identically
   to unknown numbers (no oracle). Timeouts on every outbound enrichment
   call (caller-match api source); failures fail OPEN with a recorded
   reason (a down enrichment service must not drop calls).

## Interface contracts (binding — disjoint file sets per agent)

**Slice A — lookup owner (Agent 1).** Files ONLY:
`voiceai/modules/voice/session/inbound.py` (new),
`voiceai/modules/voice/tests/test_inbound_lookup.py` (new):
- `resolve_inbound(called: str, store) -> (agent_id, InboundConfig) | None`:
  E.164-normalize `called` (strip spaces/dashes/parens, require leading
  `+`; unparseable → None, never raise), read phone-number assignments
  (read-only platform store seam, injected — never import platform
  internals, receive the store/lookup callable), return the assigned
  agent + its inbound config; unknown → None (caller rejects identically).
- Tenant rule: the returned agent's tenant binds downstream; the lookup
  itself returns no tenant enumeration (single-row-or-None).
- Unit tests with fakes: normalization matrix, assigned/unassigned,
  unparseable, multi-tenant same-number (first-assigned wins? NO —
  assignments are globally unique by number; duplicate assignment is a
  409 at assign time — read the assign path to confirm, report if not).

**Slice B — carrier webhook owner (Agent 2).** Files ONLY:
`voiceai/modules/voice/controller.py` (append endpoint only),
`voiceai/modules/voice/session/composition.py` (greeting-override hunk only:
apply `resolve_greeting` result to the welcome path, documented narrow edit),
`voiceai/modules/voice/tests/test_inbound_webhook.py` (new):
- `POST /voice/inbound/twilio` (form-encoded Twilio body: `To`, `From`,
  `CallSid`): validate signature (env token; failure → identical reject);
  normalize → Slice A lookup → Slice C screening → respond TwiML (connect
  greeting flow) or reject TwiML. No agent logic here (thin layer, ≤30
  lines/handler per Rule 1f); no repository access (service/seam calls).
- Tests through the real app factory (httpx, form posts): happy path,
  unknown number, bad signature, blocked caller — reject shapes identical
  where the contract demands it.

**Slice C — screening owner (Agent 3).** Files ONLY:
`voiceai/modules/voice/static_methods.py` (append pure functions only),
`voiceai/modules/voice/tests/test_screening.py` (new):
- Pure functions: `is_blocklisted(caller, blocklist) -> bool` (E.164
  normalize both sides); `spam_verdict(...)` (stub engine honoring the
  `spam_protection` flag: off → pass-through documented; on → current
  capability, loud in-report — no fake ML); `caller_match_context(...)`
  (none/csv/sheets/api per `caller_match_source`; api source timeout-bound,
  fail-open with recorded reason); `resolve_greeting(inbound, agent_welcome)`
  (precedence rule, Decision 4).
- No I/O except the injected api fetch (timeout mandatory); no store access.

**Slice D — pins + docs owner (Agent 4).** Files ONLY:
`tests/arch/test_inbound_contract.py` (new), `openapi.yaml`,
`API_REFERENCE.md`:
- Mechanical pins: inbound footprint (lookup + webhook + screening files
  named; `agent_manager/` + `platform/router.py` + `platform/models.py`
  ABSENT from writers — reads of the platform store allowed only via the
  injected seam); greeting-precedence pin; reject-shape equality pin
  (unknown vs bad-signature vs blocked wire shapes, per contract).
- Docs: inbound flow, screening order, greeting precedence, record fields,
  carrier-seam extension notes for Plivo/Talko.

**Slice E — UI track owner (Agent 5, UI repo).** Files (UI repo) ONLY:
`src/components/calls/execution-drawer.tsx` (screening section only),
`__tests__/components/inbound-screening.test.tsx` (new):
- Render `inbound_screening {decision, reason}` from the call record
  (blocked/spam/passed + reason copy); absent field → section hidden (old
  records predate it). No other drawer changes.

## UI contracts (UI track, separate repo — Slice E above; existing
`inbound-config.tsx` already exposes every persisted field, no form changes
needed except greeting-override copy once Slice C lands the precedence).

## Data model

No new collections. Call record gains `inbound_screening {decision, reason}`
(additive; absent on pre-0047 records). Phone-number assignment rows read as
today (no migration; duplicates — if found — are triage, not silent
first-wins: report loudly).

## Security notes

- Webhook signature validation (env token, constant-time compare); failures
  identical to unknown-number rejects (no oracle).
- No cross-tenant enumeration via dialing (single-row-or-None + identical
  rejects, pinned).
- Enrichment fetches: `is_safe_outbound_url` + mandatory timeouts, fail-open
  with recorded reason; no secrets in logs (`redact_secrets`); PII
  (caller numbers) at identifiers-only level, never payloads.
- `make sec` clean; no new deps.

## Test plan

Slice A: lookup units (fakes). Slice B: factory webhook tests (form posts).
Slice C: pure screening matrix. Slice D: arch pins + docs match. Slice E:
drawer tests (mocked record). Integrator: full gate both repos + coverage.

## Verification

```sh
make check
make sec
```

```sh
npm run lint && npx tsc --noEmit && npm test
```

## Rollout

Additive: new endpoint + additive record field; existing persisted inbound
configs start working (behavior change by design — that is the spec).
Rollback is revert (configs persist inert again, as today). UI binds
afterwards. Duplicate-assignment triage (if any) resolved before rollout.

## Burn-down

- [x] Slice A: ingress lookup (Agent 1).
- [x] Slice B: Twilio webhook (Agent 2).
- [x] Slice C: screening pure functions (Agent 3).
- [x] Slice D: pins + docs (Agent 4).
- [x] Slice E: screening display (Agent 5, UI).
- [x] Integrator: drift + gates + report (no commit — gate-held).

## Integration notes (integrator resolutions, all loud)

- **Duplicate-assignment triage: NOT verifiable from here.** Phone-number
  rows live in Redis (Upstash), unreachable from the laptop (auth/TLS
  failure on direct scan) — and absent from Atlas entirely. The runtime
  fails closed on duplicates (None + WARNING, never first-wins), so the
  slice is safe to ship; but ops must confirm zero duplicates in prod
  Redis before rollout, and a follow-up should add the 409 + unique index
  the assign path lacks.
- **Full screening order wired in the webhook.** Slice B stopped at
  blocklist-only; the integrator added spam-verdict + caller-match calls
  (fail-open recorded, identifiers-only logs). No new reject paths —
  today's spam engine always passes honestly, match only enriches.
- **`TWILIO_AUTH_TOKEN` declared** (`core/environment.py` + `.env.sample`
  already carried it); the os-environ fallback deleted and B's tests moved
  to `Environment(twilio_auth_token=…)` (Rule 4 strict).
- **Literals promoted** (B's TODO): route + TwiML block into
  `voice/constants.py`; E.164 bounds were NEVER there (Agent A defined
  them fresh — an integrator misread briefly reverted).
- **Agent A-E.164 duplication avoided, B's route-list pin extended** with
  the constant (not the literal), D's seam anchors repointed at the split
  home, seed pin accepts the deprecated outcome, docs-match pairs updated.
- **Budgets ratcheted with citations:** voice 48400→49800.
- **Stream-connect is OUT — spec 0048 owns it.** The webhook answers
  Say/reject only; no TwiML `<Stream>` leg, no carrier WS accept, no
  screening-outcome persistence on a session record (nothing to persist
  to — the session path never sees these calls). Wiring an unauthenticated
  carrier WS route + ticket minting + composition pickup unreviewed would
  be scope creep with a threat model attached. 0048 builds: carrier WS
  accept (start-message re-lookup + re-screen, trust-but-verify),
  ticket-mint design for carrier calls, screening-outcome record emission,
  Plivo/Talko shims on the carrier seam.
