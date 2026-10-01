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

TBD.

## Rollback

Revert the owning spec's merge; no migrations in scaffold.
