# Spec 0029: tools module — global tools + sharable webhooks (Phase B)

## Goal

Tools stop being per-agent copy-paste: a `tools` collection holds
system-seeded internal tools (readable by every tenant) plus tenant tools
(full CRUD, tenant-isolated), and webhooks become a shareable tool *kind*.
Agents attach by id; sharing = attaching the same id across agents.

## Non-goals

- Inbound trigger routes (later spec).
- Admin tool CRUD beyond tenant scope; BYOK execution sandboxing.
- Migrating existing embedded tools (grandfathered; next edit resolves).
- Realtime hot-path changes; DB migrations beyond the new collection.

## Interface contracts

**Slice 1 — module + internal seed + CRUD.**
- `Collections.TOOLS = "tools"`; `ToolDefinition(BaseFields)`:
  `tool_id` (natural key, pinned as `id`), `kind` (`function|webhook|
  internal`), `name`, `description`, JSON-Schema `parameters`,
  endpoint binding (`url`, `method`, `auth_ref?`, `timeout_s`),
  `version`, `deprecated`, `tenant_id` (`system` for internal).
- System seed (code-reviewed table, catalog precedent): `hangup`,
  `transfer_call`, `knowledge_search` (the implicit engine tools made
  explicit — names/descriptions/parameters documented from engine behavior).
- Endpoints: `GET /api/v1/tools` (list: system + own tenant),
  `GET /api/v1/tools/{id}`, `POST /api/v1/tools` (tenant rows),
  `PUT /api/v1/tools/{id}` (own tenant rows only; system rows 403),
  `DELETE /api/v1/tools/{id}` (soft delete, own tenant only).
- Service enforces: tenant isolation structural (scoped repo); system rows
  immutable through tenant endpoints.

**Slice 2 — webhook kind + agent attach validation.**
- `webhook` tool kind: `url` + `auth_ref` + `params_template` + timeout;
  per-agent overrides at attach time (stored on the agent record's embedded
  entries, not the shared row — bare-string refs carry no overrides).
- Agent attach (task-level, tools bind per task like `tools_config`):
  `tool_refs[]` on `api_tools`, plus `pre_call_webhook_ref` on
  `tools_params` entries (the key sits where the runtime reads the URL —
  there is no top-level `webhook_refs[]`); write-time resolution (unknown
  id → 400 with valid values, catalog-style); ref-attached endpoints pass
  the SSRF pre-flight fail-closed (identifiers only); pre-call webhook path
  references shared entities by id (engine behavior unchanged — resolution
  at write/attach, not per-call).

**Slice 3 — UI sync spec.**
- Builder tool-picker bindings (separate spec file, UI track).

## Data model

`tools` collection (new `Collections` member). Tenant-stamped rows +
system rows; `id` = `tool_id`. No migration (new collection).

## Security notes

- Endpoint bindings hold URLs + auth *references* (never raw secrets in
  rows — secrets live in environment/secret store; rows carry `auth_ref`).
- SSRF: webhook/tool URLs validated on attach (reuse the outbound guard,
  not per-call).
- Tenant isolation structural; system rows read-only to tenants.
- `make sec` clean.

## Test plan

Slice 1: seed idempotency + census; CRUD isolation (cross-tenant 404s,
system immutability); endpoints via factory. Slice 2: attach resolution,
override merge, pre-call by-id path, 400 shapes.

## Verification

```sh
make check
make sec
```

## Rollout

Additive module + registry entry. Rollback is revert. Seed via lifespan
hook (catalog precedent: version-aware `ensure_seeded`).

## Burn-down

- [x] Slice 1: tools module + internal seed (4 rows) + CRUD + dual-view service.
- [x] Slice 2: webhook kind + agent attach validation — `tool_refs[]`
  materialize into `tools`/`tools_params` at write (embedded wins ties),
  `pre_call_webhook_ref` in `tools_params` entries stamps URL + params
  template, unknown/foreign ids 400 with visible ids (no oracle), ref-attached
  endpoints pass the SSRF pre-flight fail-closed (identifiers only;
  pre-existing embedded URLs grandfathered), pure-internal rows stamp no null
  URL, unwired compositions warn-and-skip. Wording alignments: per-agent
  overrides live in embedded/`tools_params` entries (the shared row is never
  touched — bare-string refs carry no overrides); the webhook key is
  `pre_call_webhook_ref` on the params entry (not a top-level `webhook_refs[]`
  — it sits where the runtime reads the URL); `list_tools` valid-values feed
  the 400s; deprecated rows resolve (pickers filter in Slice 3).
- [x] Slice 3: UI sync in spec 0030 (registry hooks, picker bindings,
  legacy retirement plan).
