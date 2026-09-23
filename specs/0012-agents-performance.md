# Spec 0012: Agents module enterprise-performance redesign (HLD → LLD)

## Goal

Cut agents-module latency and store load on the hot paths — call setup
(definition+prompt reads), agent authoring (extraction fan-out), and per-turn
judgments — while preserving every wire shape and legacy-parity quirk pinned by
the 100+ existing tests. New code lives in a new `agents/runtime/` seam; the
2700-line verbatim `brains/` moves are not rewritten.

## Non-goals

- No graph-runtime rewrite (routing/traversal/generation stay verbatim; client
  pooling there is a follow-up engine spec).
- No wire/contract change: CRUD envelopes, brain stream shapes, port signatures,
  `__all__` removals — none. `__all__` only gains names.
- No judgment-model changes: eager judgment-LLM construction with env-selected
  models stays (pinned by `test_simple.py:62`), as does direct attribute
  assignment in tests.

## HLD

```text
voice call setup / authoring request
  → AgentService (CRUD, parallel extraction fan-out, bounded)
  → CachedAgentReader ──hit──→ memory (TTL, per-agent generation)
       │Gateway implementing BOTH ports; write-through invalidation
       └──miss──→ MongoAgentDefinitions / MongoAgentPrompts (unchanged)
  → BrainFactory.build(agent_type, …) → brain instance (registry, additive)
  → per-turn: run_judgments(completion, voicemail, timeout) concurrently
  → KB brain: opt-in retrieval cache (rag_cache_ttl_s > 0), bounded + TTL
```

Performance budgets (new, enforced by tests where marked):

- Call-setup store reads: ≤ 1 round-trip per agent per TTL window (cache; *tested*).
- Extraction seeding: wall-time ≈ slowest single task, not the sum (bounded
  `MAX_EXTRACTION_CONCURRENCY`; *tested* for order + bound).
- Judgments: concurrent with timeout, fail-safe defaults preserved (*tested*).
- Memory: every cache bounded with TTL + size cap (AGENTS.md §5); no unbounded growth.

## LLD

### 1. `agents/runtime/` package (new; own files; strictest lint/mypy)

- `factory.py` — `BrainPort` (structural `Protocol`: the `generate`/`check_for_*`
  surface the engine consumes) + `BrainFactory` (frozen `agent_type → constructor`
  map for the 7 brains + webhook/extraction/summarization; `build()` raises
  `AgentsError` on unknown type; `register()` for tests/tenants). No legacy imports.
- `compiled.py` — `CachedAgentReader` implementing `AgentDefinitionPort` AND
  `AgentSessionStorePort` over injected inner ports. Per-agent entries
  `(payload, expires_at, generation)`; `get_*` serve memory on hit, load on miss;
  `save_*/delete_*` delegate then bump the generation (write-through
  invalidation — stale reads impossible across the writer). TTL from
  `RUNTIME_CACHE_TTL_S`; unbounded growth impossible (one entry per agent id).
  `CancelledError` propagates; cache never raises (miss-through on any error? No —
  errors propagate, only hits shortcut; a failed load is not cached).
- `judgments.py` — `run_judgments(completion_factory, voicemail_factory,
  timeout_s)` runs both coroutines concurrently (`asyncio.wait`, FIRST_COMPLETED
  per judgment with own timeout), each degrading to its legacy default
  (`{"hangup": "No"}`, `{"is_voicemail": "No"}`) on timeout/error — the same
  fail-safe the brains implement serially today.

### 2. Service parallel extraction (behavior-preserving)

`create_agent`/`update_agent`: collect pass walks tasks with the IDENTICAL
subscript/`.get` expressions (malformed tasks raise exactly as today; update-path
guard invoked once per extraction task in walk order, so `GuardSpy.calls`
counts are unchanged), then a bounded `asyncio.Semaphore
(MAX_EXTRACTION_CONCURRENCY)` + `gather` generates all prompts concurrently and
assigns back by index (order-preserving). Single-task flows are bit-identical;
N-task flows go from sum-latency to max-latency. Divergence note (accepted): on
valid inputs none; on a malformed LATER task no earlier-task generation starts
(current code starts earlier ones) — no test pins the old interleaving, and the
new order (validate-then-generate) is strictly safer.

### 3. KB opt-in retrieval cache (default OFF)

`rag_config["cache_ttl_s"] > 0` enables a per-agent bounded LRU
(`RAG_CACHE_MAX_ENTRIES`) keyed by `(query, top_k)` with TTL. Default 0 keeps
every existing test and prod flow byte-identical. Cache lives on the brain
instance (per-call scope — no cross-call leakage, no invalidation protocol
needed beyond TTL).

### 4. Container wiring (prod gets the cache; tests unchanged)

`_build_agent_service` wraps `definitions`/`prompt_store` in one shared
`CachedAgentReader` (TTL from constants): every CRUD/controller read hits memory
inside the window, every service write invalidates. Fakes in unit tests bypass
the cache entirely. Voice's `session_store` keeps its raw wiring in this spec —
routing the per-call prefetch through the same shared reader is a follow-up
(engine spec; single shared instance required so writers and readers cannot
diverge — see burn-down).

### 5. Constants (all new literals in `agents/constants.py`)

`RUNTIME_CACHE_TTL_S`, `MAX_EXTRACTION_CONCURRENCY`, `JUDGMENT_TIMEOUT_S`,
`RAG_CACHE_MAX_ENTRIES`, `RAG_CACHE_TTL_KEY = "cache_ttl_s"`.

## Data model

None. No collections, no migrations. Cache is memory-only, per-process.

## Security notes

- Cache keys are agent ids (identifiers, safe at INFO). Payloads never logged.
- Judgment timeout prevents a hung judge from stalling a turn (liveness).
- Bounded fan-out prevents LLM-provider stampede on 50-task agents (abuse §4).
- `make sec` scope unchanged.

## Test plan (all new tests colocated, offline)

- `test_runtime_factory.py`: registry builds all 7 types; unknown type raises;
  custom registration isolated per test.
- `test_runtime_compiled.py`: hit serves without touching inner; miss loads once;
  save/delete invalidate; TTL expiry reloads; directories always read through.
- `test_runtime_judgments.py`: both run concurrently (elapsed < sum); timeout and
  error degrade to legacy defaults.
- Service: 3-task extraction asserts order + `llm.calls` sequence + max
  in-flight ≤ bound (instrumented fake).
- KB cache: off by default (no cache reads); on caches identical query within TTL.
- Full existing agents suite unchanged and green.

## Verification

```sh
make check
make sec
.venv/bin/python -m pytest -q voiceai/modules/agents
```

## Rollout

Single merge, no flags (KB cache default-off IS the flag). Container-only wiring
change; rollback = revert. Cache is memory-only — revert loses nothing.

## Burn-down

- [ ] Voice prefetch through the shared `CachedAgentReader` (single instance for
  service + voice, so writer invalidation covers readers) — engine spec.
- [ ] Graph routing-client pooling (`init_routing_client` per turn) — engine spec.
- [ ] `list_agents` full-scan → indexed directory read — platform-store spec.
- [ ] Per-file `brains/` annotation (mypy-strict) — engine typing spec.
- [ ] `test_ports` SynthesizerPool drift (spec-0011 note) — resolve with tts typing.
