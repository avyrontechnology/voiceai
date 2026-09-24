# Spec 0018: Otoba platform target architecture (binding design)

> Landed from the Otoba Platform Blueprint (2026-09-24, measured against
> `feature--dev--2026`) with corrections from senior review. Every later spec
> (0019+) implements one slice of this document. The blueprint's full text
> (principles, lookup tables, topology diagram) is preserved below in
> condensed form; normative deltas vs the blueprint are marked **[CORRECTION]**.

## Goal

Turn `voiceai` into a platform where every concern has exactly one home, every
module carries its own code, tests, fixtures, contract, runbook and changelog,
no file exceeds 1,500 lines (target ≤ 800), tenants are isolated by typed
context, scoped repositories and keys, channels beyond telephony plug into one
abstraction, and agents are versioned definitions with tools, knowledge, memory
and evals. Every step is a behavior-preserving strangler move that squads run
in parallel from day one.

Measured state at landing: ~86,809 lines under `voiceai/`; `voice/` 45,256
(175 files); `task_manager.py` 2,498 (largest); 10 files over 800 lines (see
M0 allowlist note); 2 test trees per module today; `org_id` the only tenancy.

## Non-goals

No big-bang rewrite. No change to the Ten Rules, the layer matrix or the four
strangler bridges; two additive gates are the only amendments. No vendor
lock-in. No product feature build-out here: this document fixes where features
live and what they may touch.

## Ten principles (binding)

1. One home per concern — a second implementation anywhere is a gate failure.
2. Modules are the unit of ownership — nobody edits another squad's module
   without a spec naming the file.
3. Contracts, not internals — cross-module use via the other's `__all__` from
   the container, never internals.
4. Tenant context is typed and ambient (`TenantContext`; repos/keys/logs derive).
5. Channels carry frames; the runtime carries turns.
6. Providers are plug-ins with one skeleton each (reconnect/pool/switch/metrics
   live once).
7. Size is a contract — file ≤ 1,500 hard, ≤ 800 target, module budget in the
   registry, enforced by test.
8. Legacy shrinks monotonically — tagged shim or deleted; live-and-unowned is
   forbidden after its migration step.
9. Everything is offline-testable — ports ship fakes; no network/carrier/model.
10. "Where does X live" is generated, not remembered (`make docs`).

## Interface contracts (target tree + budgets + homes)

Budgets are hard module ceilings enforced by `test_size_budgets.py`.

```text
voiceai/
├── common/   errors responses pagination datetime security logger
│             tenancy.py keys.py ids.py events.py telemetry.py      ≤ 4,000
├── core/     environment container app_factory db redis resilience
│             bus.py telemetry.py middleware.py lifecycle.py workers/*.py ≤ 4,000
├── database/ base.py (BaseFields + tenant_id) constants.py (Collections, INDEXES)
│             repository.py (Repository, TenantScopedRepository)
│             migrations.py                                         ≤ 2,500
├── modules/
│   tenancy/      tenants, workspaces, plans, quotas, vault, numbers, flags   ≤ 8,000
│   identity/     users, sessions, keys, roles/scopes, invites, audit
│                 (was auth; shim one release)                                 ≤ 8,000
│   agents/       definitions (versioned), prompts, tool specs, evals,
│                 runtime brains (specs 0012/0015)                             ≤ 14,000
│   knowledge/    bases, ingestion, retrieval, RAG adapter                    ≤ 6,000
│   llm/          clients, message models, tool-call accumulation, policy     ≤ 6,000
│   conversations/ session, turn pipeline, lifecycle, language, s2s runner    ≤ 16,000
│   channels/     port + telephony/* web/ messaging/* chat/ email/             ≤ 14,000
│   media/        asr/tts/s2s providers, pools, audio utils                    ≤ 16,000
│   calls/        records, recordings, transcripts, post-call pipeline         ≤ 6,000
│   campaigns/    outbound, lists, pacing, DNC, retries                        ≤ 6,000
│   billing/      wallets, ledger, metering, pricing (was wallet)              ≤ 6,000
│   integrations/ signed webhooks, tool endpoints, MCP, CRM connectors        ≤ 8,000
│   analytics/    read models, reports, exports                                ≤ 6,000
│   notifications/ tenant-user notifications, templates                       ≤ 3,000
│   admin/        superadmin plane (audited)                                   ≤ 4,000
│   health/       liveness/readiness/dependency report                         ≤ 1,500
└── legacy (shrinking to zero)
```

Where-X-lived highlights: `common/tenancy.py` (contextvar, 4 constructors only:
HTTP middleware, channel handshakes, job runner, scheduler); `common/keys.py`
(the only key f-string, `t:{tenant}:`); `core/bus.py` (Redis Streams first);
`core/workers/*.py` (api, realtime, telephony_edge, jobs, scheduler);
`database/base.py` (+`tenant_id`), `constants.py` (tenant-first compound
indexes); `TenantScopedRepository` (ANDs tenant into every filter, stamps
inserts, refuses tenant change, foreign reads as not-found; container-only
construction); `modules/tenancy/{credentials,numbers,flags}.py`;
`modules/agents/{models/tools.py,runtime,evals/}`; `modules/llm/{policy.py}`;
`modules/knowledge` (RAG server becomes an adapter); `modules/conversations/*`
(renamed from `voice/session`, TaskManager decomposed per M5);
`modules/channels/{ports.py,telephony/<carrier>/{inbound,outbound,signaling},
web/,messaging/<provider>/,chat/,email/,service.py}`;
`modules/media/{asr,tts,s2s,audio/}`; `modules/calls` (`executions`→`calls`
via migration, postcall on jobs worker); `modules/{campaigns,billing
(metering),integrations/{webhooks,connectors},analytics (read-only),
notifications,admin (/api/v1/admin)}`; `modules/agents/templates.py` (from
wallet); gates in `tests/arch/`; one test tree per module;
`local_setup/` dev-only end-state.

Omnichannel contract: `ChannelKind{VOICE_TELEPHONY, VOICE_WEB,
MESSAGING_WHATSAPP, MESSAGING_SMS, CHAT_WEB, EMAIL}`, `Modality{AUDIO, TEXT}`,
`ChannelSession`, `InboundFrame = AudioIn|TextIn|DtmfIn|AttachmentIn|Hangup|Mark`,
`OutboundFrame = AudioOut|TextOut|Transfer|Hangup|Mark|TypingOn|Off`,
`ChannelAdapter(accept/frames/send/close)`; one carrier directory
(`inbound/outbound/signaling/constants/tests`, ≤ 1,200 lines) + one
`register()` line per provider.

Provider skeletons: ASR `connect·send_audio·events()·close` (`media/asr/pool.py`,
per-provider ≤ 400); TTS `synthesize(text, voice)` (`media/tts/stream.py`);
S2S same template (`media/s2s/base.py`, ≤ 600); LLM `stream(messages, tools)`
(`llm/{tool_calls,pool,policy}.py`, ≤ 600). Credentials only via
`CredentialResolverPort`, never env.

Agentic model: versioned `AgentDefinition` (tenant/workspace, draft/published/
archived, pinned calls); `ToolSpec` names integration tools (no raw URL/headers
on agents; model args validated, cannot override url/auth/headers); tenant KBs
+ `RetrieverPort`; per-participant memory policy; offline + on-publish evals.

Topology: api (stateless) / realtime (sessions, quota+capacity admission,
call_id affinity, SIGTERM drain) / telephony-edge (all carriers, replaces the
three compose apps) / jobs (Redis-Streams groups, idempotent, DLQ) / scheduler
(single leader). Resilience in `core/resilience.py`. Path from single VM+Redis:
M6 (jobs worker) then M9 (edge, realtime split, bus), single-process mode kept
via env.

Module template: `__init__.py` (`ModuleDef` + `max_lines`), `__all__` (public
surface only), `CONTRACT/README/RUNBOOK/CHANGELOG.md`, canonical file set,
`adapters/` (only cross-surface/legacy importer), `migrations/NNN_slug.py`
(idempotent), `tests/` (unit + controller + contract + tenant-isolation),
`make test MODULE=<name>` runs one module alone.

Mechanical gates (amendments A1/A2 + 01/02/05–09): layer contract (debt only
shrinks); module shape (extended: ports, conftest, four docs, `max_lines`);
**A1** size budgets (1,500 hard / 800 target w/ `SIZE_DEBT`+spec id / module
totals); **A2** tenant isolation (tenant repos only, no key f-strings outside
`keys.py`, no provider-key reads outside environment+tenancy, tenant
middleware everywhere); no-parallel-impl (retired ⇒ shim or absent); import
cycles (acyclic, ports break cycles); registry (owner/runbook/budget/`__all__`);
per-module cov ≥ 85% + `make dup` (30+ line blocks); CI = check+sec+cov required,
per-module path jobs, PR triggers on.

Squads own whole modules (platform-core, tenancy, runtime, channels-telephony,
channels-messaging, media, agents, knowledge, growth, billing, integrations,
sre); one spec per slice, disjoint files, one integrator; cross-squad via ports;
per-tenant flags; weekly train; on-call in every runbook.

## Data model

`BaseFields` gains `tenant_id` (system default during backfill, required after
M1) + `workspace_id`; `TENANT_OWNED` marker; tenant-first compound `INDEXES`.
New collections: tenants, workspaces, plans, credentials, numbers, flags,
knowledge_bases, documents, campaigns, campaign_contacts, webhooks,
webhook_deliveries, metering_events, notifications, agent_versions, evals.
`executions`→`calls` with dual-read for one release. Module-owned idempotent
migrations via `make migrate`; M1 backfills the default tenant from `org_id`
(kept as alias one release). Soft deletes stay (retention jobs the one argued
exception).
**[CORRECTION]** `workspace_id`: decide day-one semantics in M1 (carry it in
`TenantScopedRepository` from the start or explicitly defer — retrofitting a
second dimension later is a migration, not a refactor).

## Security notes

Isolation by construction + tenancy gate; authorization in services; admin on
its own scope; audited impersonation; AES-GCM vault (new dep: `cryptography` —
supply-chain note recorded here); tool args schema-validated with immutable
url/auth/headers + SSRF + timeouts (+ optional per-tenant egress allowlist);
HMAC stream tokens (edge mints, realtime verifies); scoped dial keys; signed
replay-safe webhooks with retry + DLQ; per-tenant/key rate limits; bounded
pagination; transcripts/phone numbers never at INFO.
**[CORRECTION]** `CachedAgentReader` keys by id only today — M1 must key it on
`(tenant, id)` (or place it deliberately above/below scoping); same for the
`executions → calls` dual-read path. Recorded here so it is not discovered
as a poisoning bug later.

## Test plan

Per-step gates in the migration table; characterization suites pin every move;
contract + tenant-isolation tests per module; offline evals; load/drain tests
for M9; `make docs` keeps the generated lookup in sync (docs gate).

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Revert per step; shims keep both paths importable for one release; moves need
no flags; new behavior (bus, realtime split, tier-2 tenancy) ships behind env
flags with single-process defaults.

## Migration plan (M0–M9, specs 0019–0032)

- M0/0019 platform-core: gates (size/tenancy/no-parallel/cycles) with
  allowlists generated from today's tree — **[CORRECTION]** 10 files over 800
  (recounted), not 11; `ModuleDef.max_lines`; scaffolder + `make docs` +
  test-tree fold + PR CI. Gates green, behavior unchanged.
- M1/0020 tenancy: context/keys/ids, scoped repository, middleware, tenancy
  module, backfill, `auth → identity` shim. **[CORRECTION]** Split M1a
  (plumbing, no behavior change) → M1b (backfill + rename + flip); audit writes
  direct-first with bus subscription later (bus lands M6 — do not invert).
  Isolation tests; all suites green.
- M2/0021 channels-telephony ∥: channels port/models/registry; `voice/io` →
  per-carrier dirs; telephony servers + compose apps → edge signaling routers;
  `place_call` moves. Telephony characterization suites.
- M3/0022 media ∥: `voice/{asr,tts,s2s}` → media; skeleton dedup to
  hooks-only ≤ 400 (covers kalpa-878); `helpers` audio → `media/audio`.
  **[CORRECTION]** spec-0017 is mid-flight (override present,
  `docs/CHECKPOINT.md` has the resume table) — finish it first, re-measure,
  then M3. Provider goldens; mypy zero.
- M4/0023 agents ∥: `voiceai/llms` → llm module + policy + credential port;
  agent_types/memory/lid shims retired. Agents + runtime suites.
- M5/0024 runtime: `voice/session` → conversations; TaskManager decomposed
  behind the frozen facade (facade ≤ 300, orchestrator/tools_dispatch/
  bootstrap ≤ 600/500/500); splits for transcript_listener/switcher/
  generation/history_sync/s2s_runner. Characterization + size gates.
- M6/0025 runtime+sre ∥: calls module (`executions`→`calls`, recordings,
  transcripts, post-call on jobs worker); `core/bus.py`; jobs worker; platform
  router/store/models retired. API parity + offline job tests.
- M7/0026 agents+knowledge ∥: versioning/publish, ToolSpec → integration
  tools, knowledge module + RAG adapter, evals. Suites + evals offline.
- M8/0027–0031 ∥: campaigns; wallet → billing + metering; integrations;
  analytics; notifications; admin; messaging/chat/email channels. Module +
  contract tests.
- M9/0032 sre+runtime: realtime split, admission, drain, scheduler, edge
  unification, autoscaling. Load + drain tests.
- End: legacy deleted; `local_setup` dev-only; no file over 800 without a debt
  entry; docs generated; no-parallel-impl list empty.
- **[CORRECTION]** Unowned oversized files need owning steps: router/store.py
  → M6 (platform retirement covers them), `helpers/utils.py` → M3 (audio
  move; the remainder needs its own split entry in M3).
- **[CORRECTION]** ULID ids: new collections only, or an explicit migration —
  `prefix_hex12` is pinned by tests and the wallet singleton today.
- **[CORRECTION]** `make dup` needs a `DUP_DEBT`-style exemption (verbatim
  parity delegators fail a naive 30-line gate on day one).
- **[CORRECTION]** UI-sync spec per breaking move (M1b/M2/M6/M8): the UI
  consumes legacy `vector-config`, executions/calls, wallet/billing and
  telephony endpoints — renames are safe only with shims or UI updates in the
  same step. `local_setup` dev-only likewise needs UI-compat coverage first.

## Burn-down

- [ ] Step zero (pre-M0): this file landed; `scripts/` → `voiceai/tooling/`
  (scaffolder + backfill/seed + test import fix — `scripts/` is gitignored so
  M0's gates would fail on a fresh clone without it); spec-0017 finished.
- [ ] M0–M9 per table, with the corrections above folded into their specs.
