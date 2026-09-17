# Contract: Enterprise Hardening (internal)

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-16

Extends `layer-contracts.md` (L-01–L-05) and `voice-module-contracts.md`
(V-01–V-05). Violations fail the gates; they are not style suggestions.

## E-01 Public facades, no deep imports

- Each module exposes ONLY its facade (`__init__.py`): service protocols,
  DTO models, error codes. Provider implementations, `services.py` /
  `repositories.py` internals are NEVER imported from outside the module.
- `protocols.py` holds the module's `typing.Protocol` ports; constructor
  injection via `core/container.py` (L-05) is the only wiring.

## E-02 Resilience on every path

- Background work goes through `TaskRegistry`/`safe_task` (never bare
  `asyncio.create_task` — banned outside `core/resilience.py`).
- Long-lived loops wrap each iteration in `iteration_guard`/`supervise`
  (log with `error_id`, backoff, continue; only cancellation + explicit
  `propagate` escape).
- Every provider await is bounded by `with_timeout`; side paths
  (telemetry, cleanup) use `call_soft` so their failure never ends a call.

## E-03 Observability without leakage

- One logger (otobaai-logger) via DI; `print()` and ad-hoc
  `logging.getLogger()` banned outside `otobaai_logger/` +
  `helpers/logger_config.py`.
- Correlation id (`new_error_id`) on every request and loop failure;
  error envelope returns it, logs carry it; no secrets, tokens, or caller
  PII in logs; exception text never spoken/returned (use the standard
  spoken fallback + `Internal error (ref <error_id>)`).

## E-04 Boundary validation + service AuthZ + tenancy

- Pydantic-v2 validation at the controller boundary (H-02); AuthN before
  dispatch (stream token, `TELEPHONY_API_KEY`, API-key principal).
- AuthZ lives in services (L-02, never assumed upstream); repositories
  are org-scoped neutral queries; `created_by`/`updated_by` stamped by
  services from the principal.
- `os.environ` readable ONLY in `core/environment.py`; secrets only via
  environment/secrets manager; least-privilege DB principals.

## E-05 Versioned, compatible surface

- HTTP routes versioned (`/v1` prefix; websocket protocol versioned the
  same way); envelope fields frozen (H-01–H-04).
- Renames/moves ship with lazy PEP-562 shims + `DeprecationWarning` +
  removal version (R-03/V-05); new code MUST NOT import shim paths;
  CI trends old-path usage to zero.
