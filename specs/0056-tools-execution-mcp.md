# Spec 0056: tools execution + vault + MCP (phased)

## Goal

Otoba AI me makun-ai Tools/MCP doc ka phased port: pehle tenant-scoped `ToolExecutor` (internal-only, chat+voice seam se), phir AES-GCM `CredentialVault` + credential/integration state (Mongo-only), phir MCP JSON-RPC/SSE client + DCR. Koi provider/endpoint hard-code nahi — sab constants/env/DB rows se, taaki naya MCP server bina redeploy ke jude.

## Non-goals

- PG lana nahi; Celery/Beat nahi; campaign pacing/DNC nahi; RAG nahi.
- Public `POST /tools/{id}/execute` route nahi (internal-only — attack surface kam).
- Legacy `agent_manager/task_manager` rewrite nahi; realtime split nahi; UI picker nahi (spec 0030 track).
- `platform/` strangler bridges nahi todna.

## Interface contracts

Owning module `voiceai/modules/tools` (`squad-platform`), additive only. Disjoint sets:
- Agent-A owns: `executor.py`, `auth_resolver.py`, `models.py` (+log), `repository.py` (+logs repo), `static_methods.py` (+pure), `constants.py` (+literals), `errors.py`/`exceptions.py`, `utils.py`/`helpers.py`, colocated `tests/` + `tests/arch` executor cases; seam files `modules/chat/adapters/llm.py` (hook call only) + `modules/voice/adapters/tools_port.py` (naya Protocol, no internals import).
- Agent-B owns: `vault.py`, `dcr_service.py` (Phase-3 me impl; Phase-2 me interface only), `database/constants.py` (additive `TOOL_EXECUTION_LOGS`, `PARTNER_CREDENTIALS`, `PARTNER_INTEGRATIONS`, `INTEGRATION_TEMPLATES`), `core/environment.py` (additive: `vault_master_key`, `tools_execute_enabled`, timeouts), `core/redis.py` (additive cache helpers), `.env.sample`, dep pins.
- Integrator owns: `adapters/mcp/*`, `core/container.py` wiring, `__init__.py` (`__all__`), `CONTRACT.md`/`README`/`RUNBOOK.md`, layer-contract delta, `make docs`.

Public functions (all typed, Google docstrings):
- `executor.execute_tool(*, tool_id, arguments, agent_snapshot, tenant) -> ToolExecutionResult` — validate schema → allowlist → SSRF pre-flight (`to_thread`) → route internal/integration/http → 401 once-retry → log (fail-open).
- `auth_resolver.resolve_headers(tool, *, force_refresh=False) -> dict` — Redis cache first, vault/DB fallback; `invalidate_credential_cache(credential_id)`.
- `vault.encrypt/decrypt(data, *, key_version) -> envelope` — AES-GCM, versioned.
- `dcr_service.register_client(issuer_url) -> ClientRecord` (Phase-3); `adapters/mcp.McpHttpClient.initialize/list_tools/call_tool` (JSON + SSE, cursor pagination).

No public execute route in Phase-1/2. Chat (`complete_chat_turn` tool_calls) aur voice (`execute_function_call` custom path) executor ko container-injected port se call karenge.

## Data model

All models inherit `database.base.BaseFields`; collections via `Collections`; soft deletes only.
- `ToolExecutionLog(BaseFields)`: `tool_id`, `agent_id`, `arguments` (redacted copy), `result_summary` (never raw secrets), `success`, `execution_ms`, `error_id?`, `tenant_id` scoped.
- `PartnerCredential(BaseFields)`: `provider`, `credential_type` (`oauth2|api_key`), `encrypted_data`, `key_version`, `expires_at`.
- `PartnerIntegration(BaseFields)`: `provider`, `agent_id`, `config`, `status`, `negotiated_protocol_version`.
- `IntegrationTemplate(BaseFields)`: `provider` UK-per-tenant (system rows curated), `credential_type`, `tools_template`, `oauth_config`, `client_config`.
`id` = natural key where applicable; tenant-first compound use via `TenantScopedRepository`; no migration (new collections).

## Security notes

- AuthZ service layer me; acting tenant `current_tenant()` se, client-supplied identity fields never trusted.
- Secrets sirf env/vault; rows me `auth_ref`/pointers; `redact_secrets` har log se pehle; transcripts/secrets INFO pe nahi.
- Outbound: `is_safe_outbound_url` pre-flight + mandatory timeout (`timeout_s` 1–120) + no blind redirects; DNS TOCTOU note — high-risk pe IP-pin follow-up.
- Error opacity: clients ko envelope + `error_id`, stack sirf logs; `str(exc)` response me nahi.
- 401 retry exactly-once; token refresh race me cache-invalidate first.
- Abuse: executor fan-out bound (`MAX_PAGE_SIZE`), per-call timeout, pagination bounded. `make sec` clean; new dep (`cryptography` use) yahi note hai — supply-chain approved.
- CORS/cookies par asar nahi.

## Test plan

Unit (service/repository/static_methods/vault/dcr with fakes) + controller/seam tests real app factory (`httpx` ASGI), offline only (`block_outbound_sockets`, fake `httpx.MockTransport`, fake Redis/DNS, Motor fake seam). Matrix: schema-invalid, allowlist-deny, SSRF-block, 401-then-success, 401-then-401 (single retry only), timeout, SSE chunked, DCR discover/register/manual-fallback, vault round-trip + wrong-key fail, log fail-open. Coverage ≥85% touched modules.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Flag-gated: `tools_execute_enabled` default off → on. Additive collections, no migration. Vault dual-read (purane `auth_ref` rows grandfathered, re-save pe encrypt). MCP `MANUAL` pehle, `DCR` per-provider allowlist. Rollback = flag off / revert. No shims.

## Burn-down

- [x] Branch `revamp/tools-execution-mcp` + ye spec.
- [x] Phase-1 executor + logs + chat/voice seam (internal-only).
- [x] Phase-2 vault + credential/integration state + env/redis.
- [x] Phase-3 MCP adapter + DCR + container wiring + docs.
- [ ] `make check` + `make sec` + `make cov` green; commit `feat(tools): execution + vault + MCP [spec-0056]`.
