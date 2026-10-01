# Spec 0049: tools route gates

## Goal

Close two holes in the tools module HTTP surface (spec 0029, slice 1 left them
open "until the threat model demands them"; the spec-0048 cutover made
`voiceai.app` the only app, so the retired `platform/` scope gates no longer
stand in front of these routes). First, every `/api/v1/tools` route carries no
authentication or scope gate: `TenantMiddleware` binds the SYSTEM tenant with
empty scopes for anonymous callers, so an anonymous `GET /api/v1/tools` answers
200 and anonymous writes reach the service. Second, a legacy-shaped body such
as `{"name": "t", "kind": "datetime"}` passes FastAPI request validation
(`kind` was typed `str`) and only fails inside
`CreateToolPayload.to_definition()`, where the row model's `ToolKind` literal
raises a pydantic `ValidationError` outside request validation — surfacing as
an opaque 500 instead of a 422. After this spec, reads require an
authenticated principal with `platform:read`, writes require
`platform:write` (the scopes the retired platform tools routes used), and
invalid bodies answer the 422 validation envelope.

## Non-goals

- Route path or shape changes (`tests/arch/test_route_inventory.py` pins the
  paths; the `data` shapes stay byte-identical).
- Per-tool ownership checks beyond the existing tenant scoping and the
  system-row 403 (spec 0029) — the scope gate is the only new authorization.
- New scopes or role changes in the auth module (`platform:read` /
  `platform:write` already exist on every role table).
- Validating `url` / `method` semantics (SSRF and endpoint checks stay at
  attach time, spec 0046 Slice C).
- Legacy `platform/` edits.

## Interface contracts

Owning module: `tools`. Files touched (this batch only):
`voiceai/modules/tools/{controller,schemas,constants,helpers,__init__}.py`
(`helpers.validation_field_paths` reduces a pydantic failure to field paths),
`voiceai/modules/tools/tests/test_controller.py` (rewritten),
`voiceai/modules/tools/tests/test_schemas.py` (new),
`voiceai/modules/tools/{CONTRACT,README,RUNBOOK}.md`, this spec.

Outside this batch's file set (one line, landed in the same working tree):
`tests/arch/test_tenant_isolation.py` — the rewritten `test_controller.py`
hand-builds a `Principal`/`TenantContext` carrying `tenant_id`, so
`TENANT_ID_FLAGGED` carries
`"voiceai/modules/tools/tests/test_controller.py",  # spec 0049: gate pins`
(the pin's own failure message asks for the entry in the same spec; the
`voice/tests/test_controller.py` entry is the precedent).

### Route gates (controller)

Every route resolves the caller the way `voice`/`voices`/`wallet` do: the
middleware-stashed principal (`request_principal`) first, else
`AuthService.authenticate(session_cookie, authorization)`; then
`ensure_permitted(principal.has_scope(scope), ...)`.

| Route | Scope | Anonymous | Wrong scope |
| --- | --- | --- | --- |
| `GET /api/v1/tools` | `platform:read` | 401 | 403 |
| `GET /api/v1/tools/{tool_id}` | `platform:read` | 401 | 403 |
| `POST /api/v1/tools` | `platform:write` | 401 | 403 |
| `PUT /api/v1/tools/{tool_id}` | `platform:write` | 401 | 403 |
| `DELETE /api/v1/tools/{tool_id}` | `platform:write` | 401 | 403 |

- 401 is the auth module's `InvalidCredentialsError` envelope
  (`code: unauthorized`); 403 is its `ForbiddenError` envelope
  (`code: forbidden`, message `Requires <scope> scope`). Both render through
  the standard error envelope — no route-local error bodies.
- The gate runs BEFORE the service call, so anonymous or under-scoped writes
  never touch the store; it runs AFTER FastAPI body validation (FastAPI
  validates parameters before the handler body executes), so a malformed body
  from an anonymous caller answers 422 — the same ordering every gated
  controller in the tree has today, and the body shape is public OpenAPI
  anyway.
- Scope names live in `tools/constants.py` (`SCOPE_PLATFORM_READ`,
  `SCOPE_PLATFORM_WRITE`); the 403 message is `SCOPE_REQUIRED_TEMPLATE`.

### Payload validation (schemas)

- `CreateToolPayload.kind` / `UpdateToolPayload.kind` are typed with the
  `ToolKind` literal (`function` | `webhook` | `internal`), so an unknown kind
  fails FastAPI request validation and answers the 422 envelope
  (`error.details.errors[].loc == ["body", "kind"]`). `internal` stays
  schema-valid and the service keeps answering 400 (`code review` message,
  spec 0029) — unchanged.
- `to_definition()` keeps a backstop: should the row model ever reject a
  payload the request schema accepted (future drift between `schemas.py` and
  `models.py`), the pydantic `ValidationError` is caught and re-raised as
  `InvalidToolError` (400, `code: invalid_request`) whose `details.fields`
  lists the offending field paths only — never the pydantic message text.
  Nothing in this spec makes that path reachable; the unit test constructs an
  unvalidated payload to pin it.

## Data model

No collection, field, index, or migration changes. `ToolDefinition` is
untouched.

## Security notes

- AuthN/Z at the controller boundary (the module has no per-tool ownership
  concept beyond tenant scoping, so the scope gate is the authorization
  surface); the service stays HTTP-free.
- Denials log through the shared `AppError` handler (identifiers + `error_id`
  at INFO/WARNING, never the body). The 403 message names the scope only.
- The 422 envelope is the shared validation handler's (`common.responses`):
  `error.message` is the fixed validation text and `error.details.errors[]`
  carries one `{loc, type}` record per failing field. When this spec was
  written the handler still emitted pydantic's full records (`msg` validator
  text and `input`, the submitted value for that field), and this spec
  disclosed that rather than touch the handler; spec 0052 closed it in the
  common layer (allow-list of `loc` + `type`, no `msg`/`input`/`ctx`/`url`).
  The controller test pins the `{loc, type}` record shape.
- No secrets, no outbound calls, no new dependencies. Rate limiting and
  idempotency are unchanged from spec 0029 (writes remain plain PUT/POST/DELETE
  behind the session or API-key gate).
- CORS unaffected (same origins, same routes).

## Test plan

Controller tests through the real app factory (`httpx` ASGI transport) with
`container.auth_service` overridden by a double that scripts one principal
(or anonymous) for the whole request — both the middleware path
(`resolve_request_identity`) and the handler fallback (`authenticate`):

- Anonymous → 401 envelope on list, read, create, update, delete (writes
  verified to leave no row behind).
- Viewer (`platform:read` only) → 200 on reads, 403 on create/update/delete.
- Owner → happy paths unchanged: 201 create, 200 read/update/delete, 404
  missing, 403 system row.
- Legacy-shaped body (`{"name": "t", "kind": "datetime"}`) → 422 envelope
  naming `body.kind`, both on POST and PUT; extra keys still 422. The value
  never reaches the response; the per-field record is `{loc, type}` only
  (shape pin, see Security notes; spec 0052).

Schema unit tests: literal kinds accepted, unknown kind rejected at
construction, `to_definition()` backstop raises `InvalidToolError` with field
paths and no pydantic text.

## Verification

```sh
.venv/bin/python -m pytest -q voiceai/modules/tools tests/arch/test_size_budgets.py \
    tests/arch/test_registry.py tests/arch/test_route_inventory.py tests/arch/test_tool_livelink.py \
    tests/arch/test_tenant_isolation.py
make lint-arch
make type
make sec
```

## Rollout

No flag: the routes were reachable only through the spec-0048 single app and
no shipped client calls them anonymously (the builder UI sends the session
cookie; API keys carry explicit scopes). Rollback is revert. The tools module
line budget rises from 1500 to 1750 for the new tests (1698 lines after this
spec; the `__init__.py` comment names it).

## Burn-down

- [x] Spec written before code.
- [x] Scope constants + 403 template in `constants.py`.
- [x] Controller gates on all five routes (`_require_scope`, voice precedent).
- [x] `kind` typed `ToolKind` on the payload models; `to_definition()` backstop.
- [x] Controller tests: 401 / 403 / 422 / happy paths; schema unit tests.
- [x] `CONTRACT.md` routes + gates; `README.md`; `RUNBOOK.md` denial note.
- [x] 422 disclosure: `CONTRACT.md`, `RUNBOOK.md` and this spec name the
      per-field records the shared handler emits; the controller test pins
      that record shape. Superseded by spec 0052: the records are now
      `{loc, type}` only and the docs + pin were updated there.
- [x] Module budget raised minimally with a spec-0049 comment.
- [x] Gates green: module tests, size/route/registry/livelink pins,
      `make lint-arch`, `make type` (full run, 531 files), `make sec`.
- [x] `tests/arch/test_tenant_isolation.py` allowlist entry for
      `voiceai/modules/tools/tests/test_controller.py` (file outside this batch's
      set — one line, landed in the same working tree; pin green, `make test`
      2014 passed).
