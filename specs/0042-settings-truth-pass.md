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

- [ ] Slice A: schema (Agent 1).
- [ ] Slice B: runtime (Agent 2).
- [ ] Slice C: report artifact (Agent 3).
- [ ] Slice D: contract pins + docs (Agent 4).
- [ ] Slice E: spec 0043 draft (Agent 5).
- [ ] Integrator: drift resolution + gate + report (no commit — Phase D gate held).
