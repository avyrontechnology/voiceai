# Spec 0002 — Agents module (tranche A of the agents/voice restructure)

- **Status:** in progress
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** spec 0001 (foundation); AGENTS.md §3.1 (strangler bridges)

## Goal

Carve the **agent definition domain** into `voiceai/modules/agents`: the agent-config schema
(today one 739-line `voiceai/models.py`), agent CRUD + prompt storage (today inlined in
`local_setup/quickstart_server.py` + `voiceai/platform/agent_records.py`), and the agent
brains (`voiceai/agent_types/`, including the 1,501-line graph agent, which exceeds the
1,500-line hard cap and MUST split on move). Strict SOLID, DI via `core/container`, no file
over 1,500 lines (target ≤ 800), and the legacy suite green after every step.

Design provenance: 11-agent workflow — 4-mapper census, 3 competing architectures, 3-judge
panel (totals 227/231/244), synthesis of the ports-and-adapters winner hardened with grafts
from both runners-up. The voice runtime is tranche B — spec 0004.

## Non-goals

- Anything under `voiceai/agent_manager`, transcribers, synthesizers, IO handlers, s2s
  (spec 0004). `voiceai/llms/` and the platform router (specs to follow). No behavior changes:
  every quirk below is preserved verbatim and tagged for its own future spec. No merge of
  `revamp/resilient-core` (its own spec).

## Design

Hexagonal strangler. `modules/agents` owns the authoring schema and an `AgentDefinitionPort`
the runtime consumes; legacy classes satisfy ports WITHOUT importing `voiceai.modules.*`;
`modules/agents/adapters`-style bridge code and legacy re-export shims follow AGENTS.md §3.1;
the engine keeps reading config as dicts (the census-verified seam), so no pydantic model
crosses the module boundary at runtime.

### Target tree (estimated lines; nothing over 1,500, target ≤ 800)

```
voiceai/modules/agents/
  __init__.py [60]      MODULE (ModuleDef) + __all__ — the ONLY import path voice may use
  constants.py [90]     bare-UUID redis-key contract, PREPROCESS_DIR ("agent_data", CWD-relative),
                        assistant_status literals ("seeding"/"updated"), route paths, file names,
                        EXTRACTION_PROMPT_GENERATION_MODEL + RAG_SERVER_URL env names, agent_type set
  ports.py [80]         AgentDefinitionPort / AgentSessionStorePort (runtime_checkable Protocols):
                        get_agent/save_agent/delete_agent/list_agents/get_prompts/save_prompts
  models/__init__.py [60]   explicit re-export of the full definition schema
  models/pipeline.py [350]  (moved: models.py 45-247, 612-679) Transcriber, Synthesizer + 12 TTS
                        provider configs, IOModel, S2SConfig, OpenAIRealtime/GeminiLive configs;
                        Synthesizer.preprocess dispatch becomes a constants class-map so the
                        package imports ZERO engine code (canary-asserted)
  models/brains.py [350]    (moved: models.py 330-583) Llm, SimpleLlmAgent, legacy Node/Edge graph,
                        Expression*, CallEvent, GraphEdge/GraphNode/GraphAgentConfig (router-cycle
                        validation intact), Knowledge*/MultiAgent, LlmAgent dispatch validator
  models/tools.py [120]     (moved: models.py 586-609, 682-697) Tool*, ToolsConfig, ToolsChainModel
                        (APIParams stays imported from voiceai.llms.types — transitional §3.1 entry)
  models/agent.py [180]     (moved: models.py 21-41, 700-739) AGENT_WELCOME_MESSAGE, LocalizedText,
                        validators, ConversationConfig, Task, AgentModel
  models/rag.py [120]       (moved: models.py 250-327) vector-store/reranker/RAG configs
  errors.py [50]        AgentsError(AppError), AgentNotFoundError, AgentConfigInvalidError,
                        PromptStoreError
  exceptions.py [40]    ensure_agent_exists / ensure_valid_agent_payload
  repository.py [250]   (adapter) RedisAgentRepository implementing AgentDefinitionPort: bare-UUID
                        keys, KEYS * + skip-":"-keys scan verbatim behind one documented method;
                        prompt file IO delegated THROUGH voiceai.helpers.utils module attributes
                        (store_file / get_prompt_responses) so existing monkeypatch targets keep
                        intercepting; raw-dict + validated-AgentModel dual return exactly as today
  service.py [250]      AgentService: CRUD, assistant_status seeding/updated injection, prompt
                        read/write, prompt-file-orphan-on-DELETE preserved, extraction_json
                        generation via LlmPort (replacing quickstart_server.py:160-175 inline
                        LiteLLM). No HTTP types (rule 1e)
  controller.py [200]   /agent, /agent/{id}, /agent/{id}/prompts, /all on the new app factory at
                        /api/v1; JSON shapes byte-identical incl. {"agents":[{"agent_id","data"}]}
                        and the 404-swallowed-to-500 quirk (# legacy-parity)
  static_methods.py [120] (moved: platform/agent_records.py 19-45) is_agent_key,
                        parse_agent_record, collect_agent_records + pure prompt-selection helpers
                        (by delegation first)
  helpers.py [200]      (adapter → moved later) render_prompt / update_prompt_with_context /
                        enrich_context_with_time_variables / structure_system_prompt — delegation
                        this phase (44 test files import voiceai.helpers.*)
  utils.py [120]        (adapter) conversation_details.json read/write glue over PREPROCESS_DIR;
                        multiagent task_1.{agent_name}.system_prompt shape (tm 2150-2162) preserved
  brains/__init__.py [30]  registry of the 7 brains + legacy_graph import-parity re-export
                        (exported, never constructed)
  brains/base.py [30] · simple.py [90] · extraction.py [30] · summarization.py [40] ·
  webhook.py [50] · knowledgebase.py [360]   (moved from agent_types/*; knowledgebase
                        RAG_SERVER_URL becomes a constructor param with env fallback — the tm env
                        write stays until composition passes it explicitly, spec 0004)
  brains/graph/__init__.py [120]  class GraphAgent KEEPS ITS NAME, composes the collaborators;
                        re-exports _DETERMINISTIC_REASONING_PREFIX (test_router_nodes imports it)
  brains/graph/traversal.py [350]   (graph_agent.py 389-805) edges/classification/process_event
  brains/graph/routing.py [400]     (256-336, 791-1073) routing client + decide/resolve chain
  brains/graph/rag.py [200]         (184-255 + retrieval glue)
  brains/graph/prompts.py [300]     (1074-1305) prompt/context/message building, tool scoping
  brains/graph/generation.py [350]  (66-183, 337-388, 1319-1501) init, generate(), aux judgments
tests/arch/modules/agents/  mirrors the tree (test_ports, test_module_def, per-file suites)
tests/arch/test_layer_contract.py  the §3.1 AST-walking enforcer (runs under make check)
```

Legacy files becoming `# legacy-shim(spec-0002)` re-exports at their old paths:
`voiceai/models.py` (superset shim — see R6), `voiceai/platform/agent_records.py`,
`voiceai/agent_types/*.py` (+ `__init__`, keeping `from voiceai.agent_types import *`
resolving for task_manager.py:62).

## Behavior-invariant checklist (normative; each preserved verbatim, tagged `# TODO(spec-NNNN)`)

Bare-UUID redis keys · `KEYS *` + skip-`":"`-keys scan · agent-404-swallowed-to-500 on
`/agent/{id}` · CWD-relative `agent_data/` (`PREPROCESS_DIR`) · prompt-file orphan on DELETE ·
`assistant_status` "seeding"/"updated" injection · `os.environ["RAG_SERVER_URL"]` side-channel
(tm 1997/2050) · the raise-a-string bug at tm:2080 (legacy call site untouched this tranche) ·
multiagent `task_1.{agent_name}.system_prompt` prompt shape · quickstart JSON shapes
byte-identical.

## Migration steps (each = one commit, one agent, disjoint ownership, universal gate)

**Universal gate:** `make check` (includes the AST layer-contract test) AND `make test-all`
with (a) zero net-new failures vs the recorded per-step baseline (the 7 known master failures
stay known — real fixes live on `revamp/resilient-core`) and (b) collected-test-count ≥ the
prior step's baseline, with a removed↔re-landed reconciliation line for any test rewrite.
Commit as `type(scope): summary [spec-0002]` quoting the gate result. A step that cannot
reach its gate is reverted, not patched forward.

- **A0 — Baseline + global guards (test-infra only).** `tests/arch/test_layer_contract.py`
  (AST walk enforcing §3.1: adapters allowlist, shim purity, agents-`__all__`-only cross-module
  imports); a socket-block autouse pytest guard (any un-mocked outbound socket fails loudly —
  dead-namespace string patches can never silently go live); a getsource meta-test asserting
  `run`, `sync_history`, `_handle_transcriber_output`, `_TaskManager__execute_function_call`,
  `_listen_transcriber` still exist on TaskManager with their pinned substrings; re-measure and
  record `pytest -q --collect-only` count + exact failing set in §Verification below.
  `task_manager.py` is excluded from `make fmt` until spec 0004 step B13b.
- **A1 — Scaffold + ports (pure addition).** `__init__`, `constants`, `ports`, `errors`,
  `exceptions` + `tests/arch/modules/agents/{test_ports,test_module_def}.py` (structural,
  import-only, offline).
- **A2 — Models split, engine-free.** `models/*` moved per the line ranges; `voiceai/models.py`
  becomes the SUPERSET shim (`from voiceai.modules.agents.models import *` PLUS retained
  `from .providers import *` PLUS the explicit stdlib/typing names transitive consumers rely
  on). Same commit: dir()-superset snapshot canary + engine-free canary (`import
  voiceai.modules.agents.models` with `voiceai.transcriber` absent from `sys.modules`).
  Named canaries: test_reasoning_effort_validation, test_router_nodes, test_s2s_providers,
  test_responses_api, test_clinic_appointment_agent, test_kalpa_synthesizer,
  test_maya_synthesizer, test_say_node_and_silence_policy.
- **A3 — static_methods + repository.** `agent_records.py` → shim (burn-down entry); prompt IO
  through `voiceai.helpers.utils` attributes; arch tests. Gate adds
  test_platform_agent_records (green via shim) and test_agent_prompts_endpoint (KNOWN-FAILURE
  STATUS PRESERVED, ported not fixed).
- **A4 — Service + additive controller.** `register(container)` binds
  repository/service/port (rule 9); controller mounts on the new app factory (additive — both
  surfaces serve); extraction_json behind LlmPort with a LiteLLM adapter; arch tests via
  httpx ASGI. Gate adds test_render_prompt, test_prompt_context_substitution,
  test_prompt_resilience.
- **A5 — Quickstart CRUD delegation.** The agent-CRUD handlers in
  `local_setup/quickstart_server.py` become one-line delegates to AgentService resolved from
  the container — routes, JSON shapes, auth deps, quirks byte-identical; module path untouched
  (Dockerfile CMD). Gate adds a curl smoke of the real app (GET /all, POST /agent, prompts
  round-trip) recorded in §Verification.
- **A6 — Six easy brains (characterization-first).** NEW characterization tests for the six
  zero-test brains land FIRST in the commit order, then the moves; every `voiceai/agent_types/`
  path becomes a shim (incl. legacy_graph import parity).
- **A7 — GraphAgent split (1,501 > cap — must split on move).** `brains/graph/*` per ranges;
  `graph_agent.py` → shim re-exporting `GraphAgent` AND `_DETERMINISTIC_REASONING_PREFIX`.
  SAME COMMIT: rewrite the 19 `voiceai.agent_types.graph_agent.*` string patches across the
  13 pinning test files (a shim does NOT redirect monkeypatch). Gate runs those 13 files
  individually + the socket guard (proves no patch went dead).

## Security notes

No new inputs or outbound calls; the extraction-prompt LLM call moves behind LlmPort with the
same env-sourced credentials. The socket-block guard is itself a security control: a dead
monkeypatch can never silently make live HTTP from tests. Redis access patterns unchanged
(`KEYS *` documented as preserved debt for the platform spec). No secrets in constants.

## Test plan

Per-module arch suites mirroring the tree; port conformance (mypy-checked assignments +
runtime_checkable isinstance on fakes); the canaries and per-step named gates above; legacy
suite as the harness with the baseline/count/reconciliation discipline.

## Verification

`make check` + `make test-all` + `make sec` + `make cov` per step; baseline snapshot and the
A5 curl smoke recorded here by their steps.

### Baseline (measured and recorded by A0, 2026-09-17)

- `.venv/bin/python -m pytest -q --collect-only 2>/dev/null | tail -1` →
  `1876 tests collected, 1 error in 0.54s` — the 1 error is the known
  `tests/test_seed_mongo_users.py` collection error (imports a git-ignored `scripts/`
  file; `make test-all` ignores that file, and with the same ignore flag the count is
  also `1876 tests collected`).
- `make test-all` → `7 failed, 1869 passed, 1 skipped`. Exact failing set (the 7 known
  master failures — never fixed in this spec):
  - `tests/test_agent_prompts_endpoint.py::test_prompts_roundtrip`
  - `tests/test_agent_prompts_endpoint.py::test_prompts_missing_file_returns_null`
  - `tests/test_agent_prompts_endpoint.py::test_prompts_missing_agent_returns_404`
  - `tests/test_prompt_resilience.py::test_missing_prompts_file_returns_empty_dict`
  - `tests/test_prompt_resilience.py::test_missing_prompts_result_supports_get`
  - `tests/test_telephony_output_send_timeout.py::test_handle_interruption_does_not_hang_on_a_dead_socket[TwilioOutputHandler]`
  - `tests/test_telephony_output_send_timeout.py::test_handle_does_not_hang_sending_audio_on_a_dead_socket`
- Pre-A0 comparison: 7 failed / 1859 passed / 1 skipped, 1866 collected. A0 adds 10
  tests (4 layer-contract + 6 TaskManager pins), zero net-new failures, and no legacy
  test tripped the new outbound-socket guard (no `allow_network` markers were needed).
  The constant +1 between collected and reported outcomes is a pre-existing
  module-level skip. `make sec` clean; `make cov` 99.23% (≥ 85%).

A0: check=green; test-all=7/1869/1876 (net-new: 0)

A1: check=green; test-all=7/1882/1889 (net-new: 0; reconciliation: `test_registry_lists_exactly_the_health_module` rewritten as `test_registry_lists_exactly_the_registered_modules` to admit `agents.MODULE` in `ALL_MODULES` — test count unchanged, +13 new agents-module tests)

A2: check=green; test-all=7/1920/1927 (net-new: 0; no test rewritten or removed — +38 new agents-models tests: behavior-parity suite, the dir()-superset shim canary against the recorded 140-name pre-move snapshot, and the engine-free subprocess canary; all 8 named canary files also run individually green)

## Risks

Shared risk register lives in spec 0004 §Risks; applicable here: R3 (dead-namespace patches —
A7), R5 (§3.1 bridges), R6 (models star-import web — A2's superset shim + canaries), R8
(resilient-core overlap: extractions are verbatim moves; overlap map in spec 0004 B0), R9
(gate integrity under suite drift), R12 (data-shaped external contracts).

## Rollout

Additive throughout; quickstart stays the deployed surface. Burn-down list of
`# legacy-shim(spec-0002)` files maintained here; deletions happen at endgame cutover specs.

Shim burn-down:

- `voiceai/models.py` (A2) — superset re-export per R6; also still the legacy home of the
  engine/provider star-exports its star-import consumers rely on.
