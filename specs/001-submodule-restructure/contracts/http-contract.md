# Contract: HTTP Surface (external)

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15

Applies to every controller route in migrated modules. Existing route paths
and externally visible behavior are frozen by this restructure (purely
structural change); this contract pins the shape around them.

## H-01 Envelope

- Success: `{ "ok": true, "detail": <payload> }` (payload shape per route,
  defined with Pydantic v2 models in the module's `models.py`).
- Error: `{ "ok": false, "detail": ..., "error": { "code",
  "message", "error_id", "retryable", "component", "details" } }` per the
  `ErrorEnvelope` in `voiceai/responses.py`.
- `message` is human-readable and safe (no raw exception text, no PII/PHI);
  `error_id` correlates to server logs; unexpected failures surface only as
  `Internal error (ref <error_id>)`.

## H-02 Input boundary

- All external input is validated by Pydantic v2 models at the controller
  before any service call; invalid input yields a `422`-class envelope
  error, never an unhandled exception.
- Authentication (stream tokens, `TELEPHONY_API_KEY` carrier auth, API-key
  principals) is verified before service dispatch; AuthZ itself lives in
  the service layer (Contract L-02).

## H-03 Pagination

- List routes use the shared `common/pagination.py` behavior: bounded
  `page_size` (default 20, max 100), total counts, empty-page (not error)
  semantics for out-of-range pages. See `../data-model.md` Entity 4.

## H-04 Compatibility during migration

- Old import paths keep resolving via lazy PEP-562 shims emitting
  `DeprecationWarning` with a removal version (see `research.md` R-03).
- No route path, status code, or envelope field changes ship as part of
  migration increments; any such change is a separate spec'd feature.
