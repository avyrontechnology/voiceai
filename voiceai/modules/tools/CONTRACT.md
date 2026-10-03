# tools CONTRACT

Owner: `squad-platform`.

## Routes

Mounted under `/api/v1` by the app factory. Every route is scope-gated
(spec 0049): anonymous callers answer the 401 envelope, under-scoped
principals the 403 envelope, and the gate runs before any service call.
Reads merge the curated system view with the caller's tenant view (tenant
rows win id ties); writes touch the tenant view only.

| Route | Scope | Answers |
| --- | --- | --- |
| `GET /api/v1/tools[?kind=]` | `platform:read` | 200 `{"tools": [...]}` |
| `GET /api/v1/tools/{tool_id}` | `platform:read` | 200 row; 404 unknown or foreign |
| `POST /api/v1/tools` | `platform:write` | 201 row; 400 `internal` kind; 422 bad body |
| `PUT /api/v1/tools/{tool_id}` | `platform:write` | 200 row; 403 system row; 404; 422 bad body |
| `DELETE /api/v1/tools/{tool_id}` | `platform:write` | 200 `{"state": "deleted"}`; 403 system row; 404; 409 referenced |

- Body (`POST`/`PUT`): `kind` (`function` or `webhook`; `internal` is
  schema-valid but answers 400 — curated in code review), `name`,
  `description`, `parameters` (JSON-Schema), `url`, `method`, `auth_ref`
  (secret-store pointer, never a raw secret), `timeout_s` (1–120),
  `deprecated`. Unknown keys and unknown kinds fail request validation:
  the 422 envelope lists them under `error.details.errors[]` as
  `{"loc": [...], "type": "..."}` (e.g. `["body", "kind"]` /
  `literal_error`, `["body", "<key>"]` / `extra_forbidden`).
- Error envelopes are the shared shape (`common.responses`): 401
  `unauthorized`, 403 `forbidden` (`Requires <scope> scope`), 404
  `not_found`, 409 `conflict`, 422 `invalid_request`. No route puts
  payloads or validator text anywhere in an error body: the shared 422
  handler reduces each per-field record to `loc` + `type` (spec 0052) —
  no `msg`, `input`, `ctx` or `url` — and `tests/test_controller.py` pins
  that record shape. Clients render field errors from `loc` / `type`.

## Events

- in: none.
- out: none (the live-link cascade is a same-call service hop, not an event).

## Collections

- `tools` (`Collections.TOOLS`): `ToolDefinition` rows, `tenant_id`-scoped;
  `tenant_id="system"` rows are the curated seed every tenant reads.

## Live-link (spec 0046)

Shared tools are live-linked: editing a tool re-materializes every attached
agent in the same service call; reads keep serving the stored snapshot, so
the hot path never gains a lookup.

- Version stamp: `update_tool` bumps `tools_version` and re-materializes all
  attached agents (bounded `tool_refs` lookup, same tenant only), returning
  the propagation count.
- Refuse-delete: `delete_tool` answers 409 with the referencing agent count
  while references exist — never a silent cascade-detach.
- Deprecation gate: `deprecated=True` blocks NEW attaches (400 naming the
  tool); already-attached agents keep running and read back flagged with
  `stale_deprecated` plus version drift (read-only metadata, never trusted,
  never re-written on read).
- SSRF parity: embedded `url` / `tools_params` endpoints pass the same
  `is_safe_outbound_url` pre-flight (`_is_url_safe`, off-loop) as ref
  endpoints at attach time; failures are 400s naming the path, never the
  URL. Grandfathered rows keep running until re-saved.
- Seed shape: every seeded row is attachable — `webhook:pre_call_notify`
  carries a real endpoint (unseeded otherwise); no attachable-but-broken rows.

## Execution (spec 0056, internal-only)
No public execute route: chat (`complete_chat_turn` tool_calls) and voice
(`execute_function_call` custom path) call `ToolExecutor.execute_tool`
through the container-injected port. Pipeline: MCP-namespace strip →
allowlist (agent `tool_refs`; deny = 403 identifiers-only) → row resolve →
JSON-Schema validate → SSRF pre-flight off-loop → internal/HTTP route →
401 invalidate + `force_refresh` + exactly-once retry → fail-open
`tool_execution_logs` row (`Collections.TOOL_EXECUTION_LOGS`). Upstream
failures are `success=False` values with `error_id`, never transport text.
Secrets live in `partner_credentials` (vault envelopes); rows carry
`auth_ref` pointers; `redact_secrets` before every log. MCP: `McpHttpClient`
(handshake, paginated `tools/list`, `tools/call`, JSON + SSE with
  `Mcp-Session-Id` resume); DCR: RFC8414/7591 discover → register, MANUAL
  short-circuit when no `registration_endpoint`.

## MCP server pilot (spec 0057, flag-gated, internal distribution only)

`POST /api/v1/mcp` (Streamable HTTP, JSON-only): `initialize` →
capabilities + `Mcp-Session-Id` header; `notifications/initialized` → 202;
`tools/list` → cursor pages of tenant-visible non-deprecated non-`internal`
descriptors (canonical `{kind}:{name}` ids); `tools/call` → guarded
`ToolExecutor` execution. Raw JSON-RPC answers (documented protocol
exception to the envelope rule — only 401/403/404 gates use the envelope).
Auth: API-key bearer with `platform:read` (`mcp:invoke` lands with OAuth in
S2). Pilot allowlist = registry visibility, NOT per-agent refs (per-agent
binding lands with `actions:` in S2). Disabled flag answers 404.

## Integrations (spec 0058)

Vault-state HTTP surface for the Integrations UI section. Secrets cross the
wire exactly once (plaintext in the creating `POST` response as
`secret_once` / `client_secret_once`); every read projects metadata only
(`provider`, `credential_type`, `key_version`, `expires_at` — the
`encrypted_data` field has no serializer, pinned by test).

| Route | Scope | Answers |
| --- | --- | --- |
| `GET /api/v1/integration-templates` | `platform:read` | 200 `{"templates": [...]}` (system + tenant, tenant wins) |
| `POST /api/v1/integration-templates` | `platform:write` | 201 row + `replaced` (tenant override; system rows stay curated) |
| `DELETE /api/v1/integration-templates/{provider}` | `platform:write` | 200 deleted; 403 system row; 404 foreign/missing |
| `POST /api/v1/integration-templates/{provider}/sync-tools` | `platform:write` | 201 `{created, updated, skipped, tool_ids, problems}`; 400 no-credential/unsafe-base; 404 unknown |

## Transports (spec 0060: `http` generic-REST + `mcp` auto-discovery)

Templates carry `transport` (`http` default — old rows stay on the manual
path, no migration). `http` sync uses the manual entries; `mcp` sync runs
`initialize` → bounded `tools/list` against `client_config.base_url` and
materializes `function:{remote}` rows (`parameters=inputSchema`,
`meta{transport:"mcp", mcp_tool}`), ignoring manual entries (reported).
MCP discovery/execution ride bearer/oauth2-token/custom-header credentials
(`api_key`/`basic` fail with guidance); query-placed keys cannot ride MCP.
The executor routes `meta.transport == "mcp"` rows through the MCP client
(handshake + `tools/call`, one refresh + retry, failures as `success=False`
values with `error_id` — same shape as HTTP).
| `GET /api/v1/partner-credentials` | `platform:read` | 200 `{"credentials": [...]}` metadata only |
| `POST /api/v1/partner-credentials` | `platform:write` | 201 metadata + `secret_once` + `replaced` (idempotent by provider) |
| `DELETE /api/v1/partner-credentials/{id}` | `platform:write` | 200 deleted; 404 foreign/missing |
| `GET /api/v1/partner-integrations[?agent_id=]` | `platform:read` | 200 `{"integrations": [...]}` |
| `POST /api/v1/partner-integrations` | `platform:write` | 201 row; 400 without a live credential |
| `DELETE /api/v1/partner-integrations/{id}` | `platform:write` | 200 deleted; 404 foreign/missing |
| `POST /api/v1/integrations/dcr-register` | `platform:write` | 201 credential metadata + `client_secret_once`; 400 undiscoverable/MANUAL |
