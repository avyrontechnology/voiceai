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
