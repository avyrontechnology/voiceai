# Spec 0060: custom integrations for http_generic AND mcp transports

## Goal

Custom integrations dono duniya me first-class: **`http` (generic REST —
aaj ka manual schema flow)** aur **`mcp` (remote MCP server — auto-discover
`tools/list`, MCP protocol pe execute)**. Template pe `transport` select
hota hai creation time pe; sync aur executor usi se branch karte hai. Dono
transport same auth vault, same audit, same allowlist pe chalte hai.

## Non-goals

- MCP OAuth *inside* discovery beyond bearer/custom-headers (DCR-issued
  tokens work as bearer; api_key/basic against MCP servers → 400 with
  guidance, S2 if demanded).
- MCP resources/prompts/sampling; `actions:` namespace; streaming tool
  results (bounded JSON only).
- Legacy `/integrations` platform routes ko chhedna nahi.

## Interface contracts

Owning module `voiceai/modules/tools` (`squad-platform`), additive only.

- `IntegrationTemplate.transport: "http"|"mcp"` (default `"http"`; unknown
  → 400 at save). `client_config["base_url"]` doubles as the MCP endpoint
  URL for `mcp` templates (SSRF pre-flight reused).
- `sync_tools(provider)` branches:
  - `http`: aaj ka entry flow (unchanged, byte-identical incl. problems).
  - `mcp`: ignores manual entries (reports them as `skipped` with reason);
    `initialize` handshake (`McpHttpClient`) → `list_all_tools` (bounded) →
  materialize `function:{remote}` rows (`parameters=inputSchema`,
  `url=endpoint`, `method=POST`, `auth_ref=cred:<id>`,
  `meta{synced_from, transport:"mcp", mcp_tool:<remote>}`). Discovery
  needs bearer/oauth2-token/custom-headers credentials (`api_key`/`basic`
  fail with guidance; `none` rides public servers unauthenticated). Invalid
  remote names → `problems` (skipped), valid ones still sync.
- Executor `_execute_checked`: rows with `meta.transport == "mcp"` route to
  `_execute_mcp` (SSRF pre-flight + `resolve_auth` headers + `McpHttpClient`
  `initialize`→`call_tool(remote, args)`; transport/JSON-RPC failures →
  `success=False` values with `error_id`, same shape as HTTP failures;
  401-retry applies through the shared path — MCP servers answering 401
  get exactly one refresh too).
- `McpHttpClient`: additive optional `extra_headers` (custom-header creds;
  `access_token` stays the bearer seam). No signature breaks.
- UI builder: transport toggle (Generic HTTP / MCP server). MCP mode shows
  Server URL + auth (bearer/oauth2-token/custom only — others explain why
  not) and hides the manual tools editor ("tools auto-discover on publish");
  review/publish flow identical (save → connect/DCR → sync → counts).
  Template cards show a transport badge; `saveTemplateSchema` gains
  `transport` (default `"http"`).

## Data model

- `IntegrationTemplate.transport: str = "http"` (new, default keeps old rows
  on the http path — no migration, readers `.get` with default).
- Synced MCP rows: `meta{synced_from, transport:"mcp", mcp_tool}`; re-sync
  ownership rule unchanged (only own stamped rows update).
- Deletes stay soft. No new collections.

## Security notes

- Discovery is an authenticated outbound call: SSRF pre-flight on the
  endpoint, mandatory timeout, no blind redirects; discovered schemas are
  data (validated as objects before materializing — a hostile server cannot
  inject rows with bad shapes, only `problems` entries).
- Remote names become row names only after slug validation (same rule as
  manual entries); `auth_ref` always `cred:<live-id>` (never server-supplied
  strings).
- Secrets: discovery/execution headers from the vault via the store (never
  logged, never echoed); error opacity + `error_id` unchanged.
- AuthZ: service layer; tenant-scoped; foreign → 404 no-oracle.
- `make sec` clean; no new dep.

## Test plan

Unit (transport validation; mcp sync happy/invalid-name/unsupported-auth/
unsafe-endpoint/discovery-failure; executor mcp happy/timeout/unreachable/
401-retry; client extra-headers) + controller (sync mcp counts) + UI
(builder transport toggle, hook bodies), offline only (MockTransport MCP
server double: initialize + tools/list + tools/call). Coverage ≥85% touched.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Additive (1 field + 1 branch each in sync/executor + 1 UI toggle).
Existing http templates behave byte-identical (`transport` defaults).
Rollback = revert. UI builder consumes same endpoints same train.

## Burn-down

- [x] Template `transport` + validation.
- [x] MCP discovery sync + executor MCP path + client `extra_headers`.
- [x] UI transport toggle + MCP publish flow + badges.
- [x] Unit + controller + UI tests (offline).
- [x] CONTRACT + arch-gate deltas in the same spec.
- [x] Gates green (backend 2151+ passed, cov 93.9%, ruff/bandit/mypy
  clean; UI 475 passed, lint 0 errors, tsc clean).
- [ ] Commit `feat(tools): http + mcp transports [spec-0060]` + UI commit.
