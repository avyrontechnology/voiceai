# Spec 0057: MCP server pilot (read-only, 1 partner, API-key scoped)

## Goal

Bahar ke MCP clients (Claude Desktop/Inspector + hamara khud ka `McpHttpClient`
loopback) hamare tenant tools ko standard protocol se call kar sakein — ek
`POST /api/v1/mcp` Streamable-HTTP endpoint (JSON-only is pilot me; SSE `Accept`
pe bhi JSON jawab + `Mcp-Session-Id` echo). Sirf read-only-style registry
tools; mutating `actions:` namespace is pilot se bahar (S2). Demand prove
karna hai, full OAuth/DCR-host nahi.

## Non-goals

- OAuth provider / DCR-host / consent screen (S2, traction ke baad).
- `actions:` namespace (`place_call` jaisi mutating actions bilkul nahi).
- Legacy SSE alag-GET endpoint; resources/prompts (clean `-32601` degrade).
- `platform/` strangler changes; UI work.

## Interface contracts

Owning module `voiceai/modules/tools` (`squad-platform`), additive only.
- `POST /api/v1/mcp` — body: raw JSON-RPC envelope (`{jsonrpc, id, method,
  params}`); answers: `initialize` → server capabilities + protocol version;
  `notifications/initialized` → accepted (no body); `tools/list` → cursor
  paginated descriptors (`name = {kind}:{name}`, never `mcp:`-prefixed —
  prefix hamare *client* ka inbound convention hai); `tools/call` →
  `{tool, arguments}` executed via `ToolExecutor`, answer `result` or
  JSON-RPC `error` (identifiers only, never `str(exc)`); unknown method →
  `-32601`. Auth: API-key bearer with `platform:read` scope (existing
  `_require_scope` precedent); anonymous 401, under-scoped 403, before any
  service call. Controller thin (≤30 lines/handler, wiring only).
- `adapters/mcp/server.py` — `McpServerRouter`: pure envelope dispatch over
  injected `list_tools`/`call_tool` callables (no HTTP/DB types); session ids
  via `secrets.token_hex` (never `random`); methods/timeouts/version strings
  from `constants.py`.
- `core/environment.py` (additive): `mcp_server_enabled: bool = False`
  (flag-gate, default dark); `core/container.py`: server router per-request
  Factory (tenant views inside, never Singleton).
- Pilot allowlist (loud limitation, `CONTRACT.md` me pinned): `tools/call`
  allowlist = tenant-visible non-deprecated non-`internal` rows (registry
  visibility), NOT per-agent refs — per-agent binding S2 (`actions:` ke
  saath). Pilot 1 partner key pe chalta hai; key revoke = contained.

## Data model

Koi nayi collection nahi. Reads: existing `tools` system+tenant views.
Writes: existing `tool_execution_logs` (fail-open audit). No migration.

## Security notes

- AuthZ service/router boundary pe (`platform:read` scope gate first; `mcp:invoke` lands with OAuth in S2); tenant
  `current_tenant()` se, client-supplied identity never trusted.
- Secrets: API keys existing auth store me; `redact_secrets` har log se
  pehle; issued `Mcp-Session-Id` opaque random, log me identifier only.
- Outbound (tools ke apne HTTP calls): existing SSRF pre-flight + mandatory
  timeout reuse; no blind redirects.
- Error opacity: JSON-RPC `error` me `code` + safe `message` + `error_id`;
  stack/logs me. `str(exc)` kabhi wire pe nahi.
- Abuse: per-key rate-limit follow-up note (pilot me key-scope + tenant
  isolation + bounded pagination + `limit` cap); mutating tools excluded by
  construction (pilot allowlist filter).
- `make sec` clean; koi nayi dependency nahi.

## Test plan

Unit (`server.py` dispatch over fakes) + controller real app factory
(`httpx` ASGI, fake container) + loopback (apna `McpHttpClient` apne pilot
server pe — protocol drift pakdega), offline only. Matrix: handshake,
paginated list, call ok/deny(unknown id)/schema-fail, unknown-method
`-32601`, 401/403 gates, SSE-`Accept` pe JSON + session echo. Coverage ≥85%
touched modules.

## Verification

```sh
make check
make sec
make cov
```

Plus bahar ka 1 client (Inspector/Claude Desktop) se manual checklist (spec
appendix me record hoga, pilot exit criteria).

## Rollout

Flag-gated: `mcp_server_enabled` default off → 1 partner key pe on.
Rollback = flag off / revert. Additive route + router file; no migration,
no shim. Pilot exit: 2 hafte me ≥1 external client se successful calls ya
explicit no-demand note — tabhi S2 (OAuth/DCR-host + actions) khulega.

## Burn-down

- [x] `POST /api/v1/mcp` + `McpServerRouter` + container wiring + flag.
- [x] Unit + controller + loopback tests (offline).
- [x] `CONTRACT.md`/`RUNBOOK.md` pilot section + arch-gate deltas
  (route inventory, `TENANT_ID_FLAGGED`, size budget) in the same spec.
- [ ] Manual external-client checklist (exit criteria).
- [x] `make check` + `make sec` + `make cov` green (2134 passed, tools cov
  94.69%; full mypy has 3 pre-existing errors in
  `voice/session/welcome.py`, on parent, untouched); commit
  `feat(tools): MCP server pilot [spec-0057]`.
