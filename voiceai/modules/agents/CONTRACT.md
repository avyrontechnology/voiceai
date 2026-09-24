# agents CONTRACT

Owner: `squad-agents` (`#squad-agents`).

## Routes (mounted under `/api/v1`)

Agent definition CRUD plus prompt store (`/agent`, `/agent/{id}`,
`/agent/{id}/prompts`, `/all` — see `constants.py` for the exact paths).
`__all__` in `__init__.py` is the ONLY surface `voice` may import (bridge 4,
enforced by `tests/arch/test_layer_contract.py`).

## Events

- in: none (synchronous CRUD). out: none.

## Collections

- `agents` (`AgentDefinition`, id pinned to `agent_id`).
- `agent_prompts` (`AgentPrompts`, id pinned to `agent_id`).

## Performance notes (spec 0012)

- Seeding N extraction tasks costs the slowest single task (bounded fan-out
  `MAX_EXTRACTION_CONCURRENCY`), not the sum; assignment back is index-ordered.
- Call-setup reads hit the `RUNTIME_CACHE_TTL_S` read-through cache; every
  service write invalidates. Directory listings always read through.
- KB retrieval caching is opt-in (`rag_config.cache_ttl_s > 0`), per-instance,
  bounded (`RAG_CACHE_MAX_ENTRIES`); default off = legacy behavior.

## Subpackages

- `models/` — authoring schema (`agent.py`, `pipeline.py`, `tools.py`, `rag.py`,
  `brains.py`) + storage envelopes (`definition.py`, `prompts.py`).
- `brains/` — runtime agent hierarchy (simple/contextual, knowledgebase,
  extraction, summarization, webhook, graph). Legacy collaborators arrive via
  `adapters/` only. Frozen: new runtime code goes to `runtime/`, never here.
- `runtime/` (specs 0012/0015, cut over in 0024 M3) — enterprise hot-path seam + conversation-brain
  builder: `BrainFactory` (engine-kind registry; prod instance crosses into the
  engine via the `brain_factory` task kwarg — the ONLY brain path since M3), `BrainPort`, `CachedAgentReader`
  (TTL read-through over both ports, write-through invalidation; prod wiring in
  `core/container.py`), `run_judgments` (concurrent completion/voicemail
  judgments with timeout + fail-safe defaults), plus pure helpers
  (`resolve_kind`, `resolve_rag_server_url`, `inject_shared_call_context`).
  Vocabulary is engine-level (`simple_llm_agent`, `graph_agent`,
  `knowledgebase_agent`); unknown kinds raise `AgentsError`, never `raise f`.
- `adapters/` — the ONLY files here that may import legacy (`llms`, prompts).
