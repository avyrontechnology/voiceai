# Spec 0052: validation envelope opacity

## Goal

Every request-validation 422 the single app answers (spec 0048) — FastAPI's
`RequestValidationError`, raised when a path, query, header or body fails its
declared schema — is rendered by one handler,
`voiceai/common/responses.py::_validation_exception_handler`, and that handler
serialises pydantic's `exc.errors()` verbatim into `error.details.errors[]`.
Each record therefore carries `msg` (validator text), `input` (the submitted
value for the failing field), `ctx` (validator context, including the text of
exceptions raised inside validators) and, for model bodies, a pydantic docs
`url`. Reproduced through `create_app`: `POST /api/v1/auth/signup` with a
too-short password answers 422 with that password under
`error.details.errors[0].input`; a rejected token, API key or webhook secret
would be echoed the same way. `AGENTS.md §4` (error opacity) forbids exception
text and payload echo in a response. After this spec every record is reduced
to client-safe structure only — `{"loc": [...], "type": "<pydantic error
type>"}` — so a UI can still map field errors while nothing the caller
submitted, and nothing a validator said, is echoed back. Spec 0049 disclosed
the leak and pinned the old record shape so this hardening would show up as a
visible contract change; this spec is that change.

Scope of the claim: request-validation 422s only. A 422 a route raises itself
as an `HTTPException` goes through `_http_exception_handler`, which renders
the developer-authored `detail` string; the two legacy sites that still build
such a detail from pydantic text are named under Non-goals and Rollout.

The same handler's log line reported `status=400` for the 422 it answered
(`InvalidRequestError`'s class status, not the status sent), so an operator
searching the `otobaai.*` logs for the status a caller saw found nothing. This
spec also makes the line report the status actually sent.

## Non-goals

- Status codes, the envelope's other keys (`ok`, `detail`, `error.code`,
  `error.message`, `error.error_id`, `error.retryable`) and the
  `error.details.errors` key itself: unchanged.
- Route, schema or validator changes in any module.
- The legacy `voiceai/platform/router.py::_parse_definition` (line 802) and
  `_parse_workflow_definition` (line 948) helpers. They ARE served by the
  single app: `core/app_factory.py` mounts `single_app_routers()`, which
  includes `graphs_router` and `workflows_router`. Both helpers join pydantic
  `loc: msg` into `HTTPException(status_code=422, detail="Invalid graph
  definition: …")`, so graphs validate / dry-run / deploy and workflows
  validate / test-run still answer a 422 whose `detail` and `error.message`
  carry validator text (`Input should be 'llm', 'static' or 'router'`). Known
  residual: validator text only — `graphs.py` / `workflows.py` have no custom
  validators or tagged unions today, so no submitted value is echoed (verified:
  a planted value does not come back) — but pydantic `msg` can embed input for
  some error types, so the sites must move to the common reducer. The frozen
  router is outside this batch's file set; the exact edit is under Rollout.
- `voiceai/modules/agents/service.py::_patch_validation_error` (line 138): on
  agent PATCH it puts `str(ValidationError)` into a 400 message, and that text
  carries `input_value=…` and the pydantic docs URL — a real input echo on a
  400, not a 422. Outside this batch's file set; follow-up under Rollout.
- `voiceai/modules/tools/helpers.py::validation_field_paths` (owned by spec
  0049's batch): it reduces a non-request `ValidationError` to dotted paths
  for a 400 and is already opaque. Re-pointing it at the common reducer is a
  follow-up, listed under Rollout.
- Translating error `type`s into human text server-side (the client owns
  presentation; see Rollout).
- Log severity rules and the log line's format (`app_error error_id=… code=…
  status=… path=…`): unchanged. Only the value of `status=` changes, and only
  where it disagreed with the response.

## Interface contracts

Owning layer: `common`. Files touched (this batch only):
`voiceai/common/responses.py`, `voiceai/common/constants.py` (two new keys),
`tests/arch/common/test_responses.py`,
`voiceai/modules/tools/tests/test_controller.py`,
`voiceai/modules/tools/{CONTRACT,RUNBOOK}.md`,
`specs/0049-tools-route-gates.md` (Security notes + burn-down lines about the
422 disclosure), this spec.

### Public function (new)

```python
def public_validation_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]
```

In `voiceai/common/responses.py`, exported through `__all__`. Takes the
records of `RequestValidationError.errors()` (or of a pydantic
`ValidationError.errors()`) and returns one record per input record, in the
same order, holding exactly two keys:

- `loc`: the location path as a list of `str` / `int` segments (any other
  segment type is rendered with `str`), e.g. `["body", "password"]`,
  `["query", "limit"]`, `["body", "items", 0, "name"]`.
- `type`: the pydantic error type as a string, e.g. `string_too_short`,
  `missing`, `extra_forbidden`, `literal_error`, `json_invalid`,
  `value_error`.

A record without `loc` yields `[]`; a record without `type` yields `""`.
Nothing else is copied — the function is an allow-list, so a key pydantic or
FastAPI adds later stays server-side by default.

Key names live in `voiceai/common/constants.py`: `VALIDATION_KEY_LOC`
(`"loc"`) and `VALIDATION_KEY_TYPE` (`"type"`), beside `DETAIL_KEY_ERRORS`.

### 422 envelope (changed)

Before (verbatim pydantic records):

```json
{"ok": false, "detail": "Request validation failed",
 "error": {"code": "invalid_request", "message": "Request validation failed",
           "error_id": "…", "retryable": false,
           "details": {"errors": [{"type": "string_too_short",
                                   "loc": ["body", "password"],
                                   "msg": "String should have at least 8 characters",
                                   "input": "short", "ctx": {"min_length": 8},
                                   "url": "https://errors.pydantic.dev/…"}]}}}
```

After:

```json
{"ok": false, "detail": "Request validation failed",
 "error": {"code": "invalid_request", "message": "Request validation failed",
           "error_id": "…", "retryable": false,
           "details": {"errors": [{"loc": ["body", "password"],
                                   "type": "string_too_short"}]}}}
```

Applies to every request-validation failure on every route (one handler,
registered once by `register_exception_handlers`).
`_validation_exception_handler` builds `error.details.errors` through
`public_validation_errors`; the response is otherwise unchanged. It does not
apply to a 422 raised as an `HTTPException` (see Non-goals).

### Log line (changed)

`_log_app_error(err, request, *, status: int | None = None)` (private). The
line's `status=` is the status the client receives: `status` when given,
`err.http_status` otherwise. Severity follows the same value (ERROR with
`exc_info` at 500 and above, WARNING below), which is what it already was for
every existing caller. Callers:

- `_validation_exception_handler` passes `HTTP_UNPROCESSABLE_ENTITY`. Before:
  `app_error … code=invalid_request status=400 path=…` for a 422. After:
  `status=422`.
- `_http_exception_handler` passes `exc.status_code`, so an `HTTPException`
  whose status has no `AppError` twin (a 422 maps onto `InvalidRequestError`,
  a 502 onto `AppError`) logs the status it was answered with.
- `_app_error_handler` passes nothing: an `AppError` is answered at its own
  status.

The line still carries `error_id`, code, status and path only — never the
body, the rejected value or pydantic's text.

## Data model

N/A — no collection, field, index or migration. The change is a response
shape only.

## Security notes

- Error opacity (`AGENTS.md §4`): `msg`, `input`, `ctx` and `url` never leave
  the service. `input` was a payload echo (a rejected password or token came
  back in the body, and so into client logs, proxies and error trackers);
  `msg` and `ctx.error` are exception text (a `ValueError` raised in a
  validator was rendered as `Value error, <text>`).
- Allow-list, not deny-list: the reducer copies `loc` and `type` and nothing
  else, so the guarantee does not depend on knowing every key pydantic emits.
- Residual, accepted: `loc` names a position, and two positions are named by
  the caller rather than the schema — the offending key of an
  `extra_forbidden` failure (`["body", "<unknown key>"]`) and the keys of
  mapping-typed fields. Those are the caller's own key names, needed to map
  the error to a field; no submitted **value** is ever in `loc`. `type` is a
  pydantic type name or a developer-authored `PydanticCustomError` type —
  never input.
- Logging: the handler logs `error_id`, code, status and path at WARNING,
  never the body; the rejected value is not written to logs either. The
  `status=` value now matches the response (422), so incident correlation by
  status works; no new field is logged.
- Known residual, not closed here: the two legacy `HTTPException(422)` sites
  in `voiceai/platform/router.py` (validator text in `detail` /
  `error.message`) and the agent PATCH 400 in `modules/agents/service.py`
  (`str(ValidationError)`, an input echo). Both are outside this batch's file
  set and are handed to the integrator with the exact edits (Rollout).
- AuthN/Z, secrets, SSRF, rate limits, idempotency, CORS: unaffected.

## Test plan

`tests/arch/common/test_responses.py` (locally built app, `voiceai.common`
only):

- Unit, `public_validation_errors`: a full pydantic record reduces to exactly
  `{loc, type}`; order is preserved; non-`str`/`int` segments are stringified;
  missing `loc` / `type` default; records from a real pydantic
  `ValidationError` reduce the same way.
- Handler: query failure, body failure with a planted secret as the rejected
  value, a validator that raises `ValueError` carrying the planted secret,
  an `extra="forbid"` body, a nested list body, and malformed JSON — each
  record's key set is exactly `{"loc", "type"}`, the planted secret and the
  validator text are absent from the whole response text, and the envelope's
  other keys are unchanged.

- Log line (`TestHandlerLogLine`, a collecting handler on
  `otobaai.common.responses`): a request-validation failure logs exactly one
  WARNING line reading `code=invalid_request status=422 path=…` with the
  response's `error_id` and no `exc_info`; the rejected value is absent from
  the line; an `HTTPException(422)` logs `status=422`; an `HTTPException(502)`
  logs `status=502` at ERROR; an `AppError` (404) logs its own status.

`voiceai/modules/tools/tests/test_controller.py` (real app factory, the
reproduction): `test_legacy_shaped_body_answers_422` pins the new record —
`[{"loc": ["body", "kind"], "type": "literal_error"}]` — and that the
submitted `kind` value appears nowhere in the response.

Other 422 tests (`agents`, `auth`, `chat`, `voice`, `wallet` controller tests)
assert status and `ok` / `error.code` only and need no change.

## Verification

```sh
.venv/bin/python -m pytest -q tests/arch/common/test_responses.py voiceai/modules/tools -p no:warnings
.venv/bin/python -m pytest -q tests/arch -p no:warnings
make lint-arch
make type
make sec
```

## Rollout

No flag — the old shape is the vulnerability, so there is nothing to keep
dual-serving. Rollback is revert.

UI sync (client contract change): clients must render field errors from
`error.details.errors[].loc` and `.type`; `msg`, `input`, `ctx` and `url` are
gone. The builder UI's API client (`obotaai-ui`, `src/lib/api-client.ts`)
reads the envelope's top-level `detail` string, which is unchanged ("Request
validation failed"), so it does not regress; any per-field rendering has to
map `type` to its own copy (e.g. `missing` → "Required",
`string_too_short` → "Too short") keyed by the `loc` path after the leading
`body` / `query` / `path` segment. API consumers that parsed `msg` must switch
to `type`.

Follow-ups (outside this batch's file set):

- `voiceai/platform/router.py:802` (`_parse_definition`) and `:948`
  (`_parse_workflow_definition`): build the 422 detail from the common reducer
  instead of `err['msg']` —
  `details = "; ".join(f"{'.'.join(map(str, r['loc']))}: {r['type']}" for r in public_validation_errors(exc.errors()))`
  (import from `voiceai.common.responses`) — and add tests in
  `tests/test_platform_graphs.py` and `tests/test_platform_workflows.py`
  asserting `Input should be` is absent from the 422 body. Reproduced through
  `register_exception_handlers` + `_parse_definition`: the 422 `detail` reads
  `Invalid graph definition: variables.k: Input should be a valid string; …`.
  If the frozen router stays untouched until M6, this residual stands as
  documented under Non-goals.
- `voiceai/modules/agents/service.py:138`: replace `str(exc)` in
  `AgentConfigInvalidError(str(exc), …)` with a message built from
  `public_validation_errors(exc.errors())` (dotted `loc` + `type`); the
  current text echoes `input_value=…` on a 400.
- `voiceai/modules/tools/helpers.py::validation_field_paths` can be expressed
  over `common.responses.public_validation_errors` (dotted join of each
  record's `loc`) so one reducer owns the pattern.

No legacy shims.

## Burn-down

- [x] Spec written before code.
- [x] Reproduction through `create_app` (payload echo observed, test red).
- [x] `VALIDATION_KEY_LOC` / `VALIDATION_KEY_TYPE` in `common/constants.py`.
- [x] `public_validation_errors` + handler wired in `common/responses.py`.
- [x] `tests/arch/common/test_responses.py`: reducer unit tests + handler
      opacity tests.
- [x] `tools/tests/test_controller.py` pins the new record shape.
- [x] `tools/CONTRACT.md`, `tools/RUNBOOK.md`, spec 0049 Security notes and
      burn-down no longer disclose `msg` / `input`.
- [x] Gates green: `make test` (2028 passed), `tests/arch` (563 passed),
      `make lint-arch`, `make type` (531 files), `make sec`.
