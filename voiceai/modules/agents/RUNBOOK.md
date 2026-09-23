# agents RUNBOOK

Owner: `squad-agents` (`#squad-agents`).

## Alerts

- `5xx on /agent/*` — check `error_id` in logs: `AgentConfigInvalidError` means a
  bad authoring payload reached the service (client bug); `PromptStoreError`
  means the prompt backend is down (infra).
- Extraction LLM dark — `require_extraction_model` fails closed when the model env
  is unset; set it or keep extraction-gated writes disabled.

## Scaling

CRUD is stateless and safe to replicate. The prompt store is backend-agnostic
(`AgentSessionStorePort`); brains are per-call objects, no shared mutable state.

### Hot-path knobs (spec 0012, all in `constants.py`)

- `RUNTIME_CACHE_TTL_S` (60s): prod `AgentService` reads through
  `CachedAgentReader`. Suspect stale reads? Every service write invalidates, so
  check for a second writer bypassing the service (there is none in prod) before
  blaming the cache. Restarting the process clears it (memory-only).
- `MAX_EXTRACTION_CONCURRENCY` (4): bounds seeding fan-out per request. Provider
  429s during bulk authoring → lower it; slow authoring → raise it (watch provider
  rate limits, §4 abuse notes).
- `JUDGMENT_TIMEOUT_S` (8s): a hung judge degrades to keep-talking/not-voicemail
  instead of stalling the turn. Frequent timeouts = judge provider incident.
- KB retrieval cache: off unless `rag_config.cache_ttl_s > 0` (per-agent,
  bounded `RAG_CACHE_MAX_ENTRIES`). Stale answers after a KB re-index → lower the
  TTL or turn it off for that agent; the default-off path is always safe.

### Logging continuity (spec 0015)

- Brain-assembly lines (`Setting up graph/knowledge agent…`, config/URL/created)
  emit identical TEXT on the factory path, but under logger `otobaai.agents`
  instead of legacy `voiceai.agent_manager.task_manager`. Message-substring
  alerts are unaffected; logger-name filters should match both names.

## Rollback

Definitions are soft-deleted; prompts are deleted before definitions (order
matters — see `service.delete_agent`). Revert the merge; no data migration in
normal operation.
