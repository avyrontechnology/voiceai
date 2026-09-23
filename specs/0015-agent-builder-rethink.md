# Spec 0015: Agent builder rethink — vocabulary, typed dispatch, engine wiring

## Goal

Replace the v1 `BrainFactory` (spec 0012: string keys matching neither the engine
nor the authoring vocabulary, `(*args: Any, **kwargs: Any)` + `cast`, used by
nothing) with a builder that models reality: the engine dispatches on
task-level `llm_agent.agent_type` (`simple_llm_agent` default, `graph_agent`,
`knowledgebase_agent`), assembles `injected_cfg` from call kwargs through ONE
shared (currently duplicated) merge, and constructs through injectable
constructors. Wire it into prod through the sanctioned kwargs-injection bridge
with an equivalence test proving the new path builds byte-identical configs.

## Non-goals

- No multiagent-map flow changes (`composition.py` loop untouched).
- No webhook/extraction/summarization builder moves (built elsewhere; noted).
- No `raise f"..."` removal on the legacy path (untouched legacy keeps its
  TypeError; the new path raises `AgentsError`).
- No existing-test edits: patch targets (`task_manager.GraphAgent` etc.) keep
  working because the legacy inline path stays verbatim for un-injected sessions.

## HLD

```text
prod call: adapter injects BrainFactory() ──kwargs──▶ TaskManager.kwargs
  __get_agent_object: factory present?
    yes → factory.build(agent_type, llm, self)   [NEW, tested, typed seam]
    no  → verbatim legacy branches               [tests/characterization unchanged]
tests/harness: no kwarg → legacy path (all existing pins hold) +
  ONE new equivalence test (factory path builds identical injected_cfg)
```

Layer note (AGENTS.md §3.1 bridge 3): the factory INSTANCE crosses into legacy
via kwargs — receiving an injected object is not an import, so `task_manager.py`
gains zero imports (4 added lines: kwarg read + delegate + return).

## LLD

### 1. `runtime/factory.py` rewrite (replaces v1 wholesale; v1 never shipped)

- `ENGINE_KINDS: Final[tuple[str, ...]]` = (`simple_llm_agent`, `graph_agent`,
  `knowledgebase_agent`) from `agents/constants.py` (`AGENT_TYPE_*`).
- `resolve_kind(raw: str | None) -> str`: `None`/unknown → `AgentsError` with
  `details={agent_type, valid_kinds}` (retires the `raise f"..."` TypeError on
  the new path). No aliases in v1 — unknown UI values fail LOUD, not silent.
- `resolve_rag_server_url(call_kwargs) -> str`: kwarg > env > localhost default;
  OWNS the `os.environ["RAG_SERVER_URL"]` side-channel write (characterization
  pins the write — same value, same timing relative to construction).
- `inject_shared_call_context(base, *, call_kwargs, context_data, llm_config,
  buffer_size, language) -> dict`: the ~15 shared kwargs→config lines (llm_key,
  base_url, api_version, api_tools, reasoning_effort/summary, service_tier,
  overflow_llm, use_responses_api, compact_threshold) in ONE place. Pure,
  directly unit-tested. Returns the same dict mutated (legacy pattern).
- `BrainFactory`: `__init__(constructors=None)` (late-bound new-home defaults;
  copy-on-write), `register(kind, ctor)`, `build(agent_type, llm, session) ->
  BrainPort` where `session: Any` is the legacy session object (documented seam;
  all adaptation in ONE typed place). Kind extras layered after shared inject:
  graph adds aux/routing/execution keys + turn_based + execution_id;
  `buffer_size`/`language` shared. `cast("BrainPort", ...)` stays but ONLY at
  this single seam with the reason documented (heterogeneous legacy ctors).
- `BrainPort` unchanged.

### 2. `task_manager.__get_agent_object` (+4 lines, zero imports)

```python
self.agent_type = agent_type
factory = self.kwargs.get("brain_factory")
if factory is not None:
    return factory.build(agent_type, llm, self)
<verbatim legacy body>
```

### 3. `adapters/manager.build_assistant_manager` (prod wiring)

`extra["brain_factory"] = BrainFactory()` always (flows AssistantManager →
tasks → `self.kwargs`). Import is adapter-legal (bridge 1).

### 4. `agents/constants.py`

`ENGINE_KINDS` tuple (derived from the existing `AGENT_TYPE_*` constants).

## Data model

None.

## Security notes

- Unknown agent types now 500-opaque with `error_id` (was: `TypeError` text risk
  on the new path; legacy path untouched).
- No secret handling changes; `llm_key` flows through the same dict as today.

## Test plan

- Rewrite `test_runtime_factory.py`: kind resolution (incl. `None`/unknown
  `AgentsError` details), shared-inject parity (exact key set), rag URL
  precedence + env write, per-kind construction through fake ctors, register
  isolation, `BrainPort` conformance.
- New `tests/test_brain_factory_equivalence.py`: full TaskManager harness with
  fake brain classes, run WITH and WITHOUT the injected factory, assert
  identical `injected_cfg` (graph + knowledgebase), identical env writes,
  identical `tools["llm_agent"]` identity and `agent_type`.
- Existing suites untouched and green (characterization pins prove the legacy
  path is byte-identical).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge. Prod takes the new path via adapter injection; revert = legacy
path everywhere (kwarg absent). No flags, no migrations.

## Burn-down

- [ ] Delete the legacy inline branches once the equivalence test has baked one
  release (follow-up spec; the factory path is then the only path).
- [ ] Multiagent-map construction through the factory (same seam, later).
- [ ] UI/authoring vocabulary aliases (`voice`/`text`/`s2s` → engine kinds) when
  the studio needs them — fail-loud until then.
