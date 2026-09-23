# agents CHANGELOG

Owner: `squad-agents` (`#squad-agents`). Newest first. Each entry names its spec;
behavioral contracts live in `CONTRACT.md`, operations in `RUNBOOK.md`.

## spec-0015 — Agent builder rethink (2026-09-24)

Replaces the v1 `BrainFactory` (spec-0012: string keys matching neither the
engine nor the authoring vocabulary, `(*args: Any, **kwargs: Any)` + `cast`,
used by nothing) with a builder that models the real `__get_agent_object`
flow. v1 never shipped to prod — clean break, no compat shims.

### What changed

- Dispatch on task-level engine kinds (`simple_llm_agent` default,
  `graph_agent`, `knowledgebase_agent`, from `ENGINE_KINDS`); missing/unknown
  kinds raise `AgentsError` with the value + valid set (retires the legacy
  `raise f"..."` TypeError on the new path; the legacy path is untouched).
- ONE shared `inject_shared_call_context()` replacing ~15 duplicated
  kwargs-merge lines per graph/knowledgebase branch (credentials, flags with
  legacy truthiness, sizing, language); graph extras layer on top.
- `resolve_rag_server_url()` owns the kwarg > env > default precedence AND the
  pinned `os.environ` side-channel write.
- `BrainFactory(constructors).build(agent_type, llm, session)` — the legacy
  session object crosses the seam untyped (documented); all adaptation lives in
  this one typed place. Prod instance injected via the `brain_factory` task
  kwarg (bridge 3 — `task_manager.py` gains zero imports, 4 added lines);
  sessions without it run verbatim legacy branches.
- `adapters/manager.build_assistant_manager` always injects the factory, so
  prod calls build through the shared assembly.

### Proof

- `tests/test_brain_factory_equivalence.py`: real `TaskManager.__init__` twice
  per shape (legacy vs factory) — byte-identical `injected_cfg`, identical env
  writes, identical wiring. This test is what lets a follow-up delete the
  legacy branches.
- Rewritten `test_runtime_factory.py` (12 tests): resolution, merge parity, URL
  precedence, per-kind construction, registry isolation, late-bound defaults.

### Deliberately unchanged

- Legacy inline branches verbatim (all existing pins hold, patch targets live).
- Multiagent-map, webhook/extraction/summarization construction (noted as
  follow-ups). No UI/authoring aliases — unknown values fail loud until the
  studio needs them.

## spec-0012 — Enterprise-performance redesign (2026-09-24)

Hot-path latency and store-load work with zero wire/contract change. All 305+
existing tests pass unmodified (one controller test caught a real regression
during development and now guards the fix — see below).

### New files (`agents/runtime/` — the only place new runtime code may land)

| File | What | Perf effect |
|---|---|---|
| `runtime/__init__.py` | Re-exports `BrainFactory`, `BrainPort`, `CachedAgentReader`, `run_judgments` | — |
| `runtime/factory.py` | `BrainFactory`: registry resolving `agent_type` → constructor for the 7 shipped brains (`llm_agent`, `other`, `knowledgebase_agent`, `graph_agent`, `extraction_agent`, `summarization_agent`, `webhook_agent`); `register()` for tests/tenants; unknown types raise `AgentsError`. Late imports keep package load light | Centralizes per-call brain resolution; no scattered construction |
| `runtime/compiled.py` | `CachedAgentReader`: implements BOTH `AgentDefinitionPort` and `AgentSessionStorePort` as a TTL (`RUNTIME_CACHE_TTL_S` = 60s) read-through cache with write-through invalidation; directories always read through; one entry per agent id; preserves the unconfigured-store 503 | Call-setup definition+prompt reads drop to ≤ 1 store round-trip per agent per TTL window |
| `runtime/judgments.py` | `run_judgments()`: runs hangup + voicemail judgments concurrently with per-judgment timeout (`JUDGMENT_TIMEOUT_S` = 8s) and legacy fail-safe defaults (`{"hangup": "No"}`, `{"is_voicemail": "No"}`); shutdown cancellation always propagates | Turn judgments cost the slower of the two, not the sum; hung judges can't stall turns |

### Modified files

| File | Change |
|---|---|
| `service.py` | `create_agent`/`update_agent` collect extraction jobs with the identical legacy walk (same subscripts, UPDATE-only guard once per task in order), then generate concurrently under `MAX_EXTRACTION_CONCURRENCY` = 4 semaphore, assigning back by index (order-preserving). N-task seeding goes from sum-latency to slowest-task |
| `brains/knowledgebase.py` | Opt-in retrieval cache: `rag_config["cache_ttl_s"] > 0` enables a per-instance bounded (`RAG_CACHE_MAX_ENTRIES` = 32) TTL cache keyed by `(query, top_k, collections)`; hits replay messages+metadata verbatim; only the success path stores; deep copies both ways. Default 0 = legacy behavior, byte-identical |
| `constants.py` | New section: `RUNTIME_CACHE_TTL_S`, `MAX_EXTRACTION_CONCURRENCY`, `JUDGMENT_TIMEOUT_S`, `RAG_CACHE_MAX_ENTRIES`, `RAG_CACHE_TTL_KEY` |
| `__init__.py` | `__all__` gains `BrainFactory`, `BrainPort`, `CachedAgentReader`, `run_judgments` (additive only) |
| `core/container.py` | `_build_agent_service` wraps both stores in one shared `CachedAgentReader`; unit-test fakes bypass it |
| `CONTRACT.md` / `RUNBOOK.md` | Runtime subpackage, perf notes, and knob tuning documented |

### New tests (21, all offline)

- `test_runtime_factory.py` — registry coverage, arg forwarding, override isolation, unknown-type error.
- `test_runtime_compiled.py` — hit/miss/invalidate/TTL-expiry/directory read-through/`None` payloads.
- `test_runtime_judgments.py` — concurrency (elapsed < serial sum), timeout + error degradation, cancellation propagation.
- `test_service_extraction_concurrency.py` — 3-task order + overlap proof, 8-task bound proof, guard-count parity.
- `brains/test_retrieval_cache.py` — off-by-default, hit replay, key sensitivity, failures never stored.

### Regression caught by the suite

Wrapping the stores broke the unconfigured-store 503 (`list_agents` read through a
`None` inner into `AttributeError`/500). Fixed by giving `CachedAgentReader` the
same `_require_definitions` 503 defense the service has; the controller test pins it.

### Deliberately unchanged

- Eager judgment-LLM construction + env-selected models (`test_simple.py` pins).
- `brains/` internals (frozen verbatim moves); graph routing-client pooling,
  `list_agents` full scan, voice prefetch sharing, and per-file `brains/`
  annotation are follow-up specs (see spec-0012 burn-down).
- No wire, route, collection, or `__all__`-removal change. Rollback = revert;
  the cache is memory-only.

## spec-0010 — Repo scaffold (2026-09-23)

`CONTRACT.md` / `README.md` / `RUNBOOK.md` created; registry ownership
(`squad-agents`); module-shape gates. No behavior change.

## spec-0002 — Agents strangler A0–A7 (history)

Schema/models migration, `brains/` verbatim moves with legacy shims,
Mongo definitions/prompts, service CRUD with quickstart parity, graph split.
Baseline the performance work builds on.
