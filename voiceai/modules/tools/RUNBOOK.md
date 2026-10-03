# tools RUNBOOK

Owner: `squad-platform` (#squad-platform).

## Alerts

- 401/403 spikes on `/api/v1/tools` after spec 0049 are the intended posture:
  callers must send the session cookie or an API key carrying
  `platform:read` (reads) / `platform:write` (writes). Correlate by the
  envelope's `error_id` in the `otobaai.*` logs; the body never carries the
  credential.
- 422 on `POST`/`PUT` (a legacy-shaped `kind` is the usual cause): the body
  comes from the shared validation handler (`common.responses`) and names
  only the failing field's `loc` and the pydantic error `type`
  (`literal_error` for an unknown kind, `extra_forbidden` for an unknown
  key, `missing` for an absent one) — never pydantic's message or the
  submitted value (spec 0052). The rejected value is not logged either, so
  ask the caller for the request body; correlate by `error_id`. The log
  line is `app_error error_id=… code=invalid_request status=422
  path=/api/v1/tools…` at WARNING — `status=` is the status the caller
  received (spec 0052; it read `status=400` for these before).

## Scaling

- Executor is stateless per request (container Factory): scale API workers
  horizontally; the shared `httpx` pool is per-execution (no cross-request
  socket sharing). Redis auth cache (300s TTL) absorbs credential reads;
  `None` cache degrades to DB/vault reads (slower, still correct).
- `tool_execution_logs` grows per call: paged reads only (`MAX_PAGE_SIZE`),
  archive/retention is a follow-up spec (no TTL yet — do not add one here).
- MCP `list_all_tools` pagination is page-bounded (default 50); a runaway
  server stops there and logs. DCR discovery is 3 metadata GETs max per
  issuer; cache the issuer decision in the template row, not in code.

## MCP server pilot ops (spec 0057)

- Dark by default (`MCP_SERVER_ENABLED=false` → 404 before any service
  call). Enable for the one pilot key only; rollback is flag-off.
- Partner offboard = revoke the API key (existing auth revoke path) + flip
  the flag off. Audit via `tool_execution_logs` (`agent_id=mcp:<tenant>`).
- 401/403 spikes on `/api/v1/mcp` are the gate posture (same as `/tools`):
  correlate by envelope `error_id` in `otobaai.*` logs.

## Rollback

Revert the owning spec's merge; no migrations in scaffold.
