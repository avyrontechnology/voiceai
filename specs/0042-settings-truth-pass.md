# Spec 0042: settings truth pass — every key proven or removed + recording

## Goal

Every call-behavior key an agent can carry must either demonstrably affect a
live call or stop shipping: the audit proved ~15 of 21 `ConversationConfig`
keys wired, `interruption_backoff_period` + `ambient_noise` stored/shown but
never read, `call_terminate` web-only, no recording toggle at all, and a set
of live-but-hidden legacy keys (`call_hangup_message`,
`welcome_message_delay`, …) unreachable through validated writes. This spec
closes all of it, and adds the launch-required recording toggle.

## Non-goals

- Inbound engine (spec 0047 owns it; `/inbound` persistence untouched).
- Tool propagation semantics (spec 0046).
- Validated dynamic extension (spec 0043) — Slice C promotes the known
  hidden keys to first-class homes only; the general mechanism is 0043.
- UI implementation (UI track follows per-slice contract notes; no UI files
  in this repo change here).
- Legacy `agent_manager/` edits: forbidden unless a runtime seam is
  unreachable otherwise — loud deviation, integrator approval in-report.

## Decisions (approved)

1. Prove-or-remove per key (no third state): a key the runtime cannot read
   is deleted from schema + UI contract + docs in the same slice.
2. Recording is built (launch requirement): `recording: bool` on the
   conversation config → runtime capture flag → artifact URL on the call
   record (extends the `recording_url: None` stub, not a new collection).
3. `call_terminate` extends past its web-only guard to telephony, or the
   schema scopes it to web explicitly — same prove-or-remove rule.
4. Hidden legacy keys get first-class schema homes with runtime reads kept
   byte-identical (no behavior change, only reachability).

## Interface contracts (binding — disjoint file sets per agent)

**Slice A — schema owner (Agent 1).** Files ONLY:
`voiceai/modules/agents/models/agent.py`,
`voiceai/modules/agents/tests/test_settings_truth_schema.py` (new):
- Delete `interruption_backoff_period` + `ambient_noise` IF Slice B cannot
  wire them (coordinate via integrator; default is delete — removal is the
  burden-of-proof loser). Any other key Slice B proves dead dies here too.
- Add `recording: bool = False` to `ConversationConfig` (exact name;
  builder contract below).
- Promote hidden keys to first-class fields with identical runtime meaning:
  `call_hangup_message: str | None`, `welcome_message_delay: float | None`,
  plus any further hidden key Slice B's trace proves live (loud in-report;
  closed set, no general passthrough — that is 0043).
- Schema unit tests: defaults, validation, explicit-not-synced round-trips.

**Slice B — runtime owner (Agent 2).** Files ONLY:
`voiceai/modules/voice/session/interruption.py`,
`voiceai/modules/voice/session/config.py`,
`voiceai/modules/voice/session/composition.py`,
`voiceai/modules/voice/session/lifecycle/hangup.py`,
`voiceai/modules/voice/tests/test_settings_runtime.py` (new):
- Wire the two contested keys or report them dead (report = Agent 1
  deletes; no silent keeps).
- `recording` flag: config → ambient capture enable (replacing the
  leg-derived `should_record` default; explicit flag wins, absent keeps
  legacy derivation — no behavior change for existing rows).
- `call_terminate` on telephony legs (extend `hangup.py:507-512` past the
  web-only guard with leg-appropriate semantics + test) or scope the schema
  (Agent 1) — decided in-slice, loud in-report.
- Runtime tests with fakes: each wired key has a consumer pin (setting on →
  runtime effect observed; setting off → absent).

**Slice C — report artifact owner (Agent 3).** Files ONLY:
`voiceai/modules/voice/session/lifecycle/report.py`,
`voiceai/modules/voice/models.py` (recording fields only),
`voiceai/modules/voice/tests/test_recording_report.py` (new):
- Call record carries the capture outcome: artifact URL (or explicit
  `None` + reason when disabled/failed — never a silent missing key).
- Reads the recording flag through existing seams only; `composition.py`
  is Agent 2-owned (do not touch — report deviations instead).
- PII rule: identifiers in logs, never audio/payloads.

**Slice D — contract owner (Agent 4).** Files ONLY:
`tests/arch/test_settings_truth.py` (new), `openapi.yaml`, `API_REFERENCE.md`:
- Mechanical pins: every `ConversationConfig` field has either a runtime
  consumer (allowlisted `file:line` map in the test) or is absent from
  schema+UI contract; the test fails on drift in either direction.
- Docs updated to match (dead keys removed, recording documented).
- `specs/0042*` stays integrator-owned (no agent edits).

**Slice E — spec 0043 author (Agent 5).** Files ONLY:
`specs/0043-dynamic-agent-config.md` (new, from `specs/TEMPLATE.md`):
- Draft the validated-extension mechanism (namespaced passthrough vs
  allowlist rule, audit walk, builder contract, migration story: none).
- No code. Findings from Slices A–C incorporated at integration.

## UI contracts (UI track, separate repo — for the record, not this build)

- Builder `call-behavior-config.tsx`: removed keys disappear; `recording`
  toggle added; `call_terminate` copy scoped per Slice B decision; hidden
  keys appear once Slice C promotes them (schemas in `lib/schemas/agent.ts`
  mirror).
- No `agent_type`/channel work here (spec 0045).

## Data model

No new collections. `recording: bool` on the conversation config (validated
write path); call record gains the capture outcome fields (Agent 3). All
persisted models inherit `BaseFields` (unchanged). Deletes N/A.

## Security notes

- Recording is PII-adjacent: artifact access follows existing call-record
  auth; retention note required in the slice report; never log payloads.
- `call_terminate` on telephony changes hangup behavior — abuse review in
  the slice report (who may set it: existing admin gates, unchanged).
- `make sec` clean; no new outbound calls; no new dependencies.

## Test plan

Slice A: schema units. Slice B: runtime consumer pins (fakes). Slice C:
report artifact units. Slice D: arch drift pins + docs match.
Integrator: full gate + coverage on touched modules.

## Verification

```sh
make check
make sec
```

## Rollout

Additive settings + additive report fields; deletions are of keys proven
unread (no live call can depend on them — the proof is the gate). Rollback
is revert. UI binds in its own track afterwards.

## Burn-down

- [x] Slice A: schema (Agent 1) — dead keys deleted, recording + 2 promotions landed.
- [x] Slice B: runtime (Agent 2) — backoff + recording + telephony call_terminate wired, 37 cases.
- [x] Slice C: report artifact (Agent 3) — capture-outcome triple + upload finalizer contract.
- [x] Slice D: contract pins + docs (Agent 4) — 6 mechanical pins, openapi/reference in sync.
- [x] Slice E: spec 0043 draft (Agent 5) — namespaced passthrough, 3 OPENs, 6-slice burn-down.
- [x] Integrator: drift resolution + gate + report (no commit — Phase D gate held).

## Integration notes (integrator resolutions, all loud)

- **Backoff restored.** Slice A deleted `interruption_backoff_period` per the
  delete-default; Slice B then wired it honestly (gate hold in the
  interruption manager, default 0). Integrator restored the field with
  consumer pins and flipped Slice A's absence tests to a default-0 pin.
  `ambient_noise` stays deleted (no honest runtime meaning without
  `agent_manager/` edits — forbidden).
- **call_terminate default 90 kept on both legs.** Slice B's extension means
  telephony rows now cap at the schema default unless configured — the ONE
  product-visible behavior change in this build (previously uncapped on
  telephony). Kept per Decision 3 (extend, approved); mitigation is raising
  the default or per-agent values. Reason code split:
  `TELEPHONY_CALL_MAX_DURATION_REACHED` added (web member untouched —
  historical data preserved).
- **recording default False is explicit, not absent.** Validated writes
  materialize explicit-False, which overrides leg-derived capture (Slice B's
  None-sentinel only sees raw-dict paths). Privacy-safe and intended: no
  capture unless opted in. No user-visible regression — nothing ever
  surfaced leg-derived capture (the report stub was always `None`).
- **Three more hidden keys promoted** (Slice A left them open; integrator
  verified each chain): `discard_pre_welcome_utterance`,
  `language_injection_mode` / `language_instruction_template`,
  `end_call_tool_mode` — all `str|bool|None` tolerant (strict Literals would
  newly reject legacy rows the runtime tolerates today).
- **welcome_message_delay pinned milliseconds.** `welcome.py` sleeps ms;
  `health.py`'s "seconds" comment was the only lie (math already consistent
  under ms) — comment fixed, zero behavior change.
- **Upload finalizer wired in legacy `run()`.** The single S3 site now calls
  `apply_recording_upload` (4 lines, existing bridge import, no new edges) —
  otherwise every recorded call would persist `pending_upload` forever.
  Narrow exception to the legacy-edit ban, approved here by the integrator.
- **Literal hygiene skipped.** New config key-name literals stay at their
  parse sites: no precedent exists (`voice/constants.py` holds wire keys,
  not config keys) and single-use sites don't trip Rule 8.
- **Budgets ratcheted, not weakened:** agents 14700→14900, voice
  47200→48200 (precedent-cited bumps); `lifecycle/report.py` (950 lines)
  registered in `FLAGGED_RESIDUALS` + `SIZE_DEBT` under spec-0042 (owning
  split TBD); canonical-file budget untouched (auth split precedent holds).
- **Spec 0043 review:** namespaced-past-through mechanism approved as
  drafted; OPEN-1 (agent-wide bag) stays deferred; OPEN-2 bounds are
  calibratable constants; OPEN-3 correctly assigns the read API to the first
  consuming spec. Ready to build when scheduled.
