# Contract: Layer Interfaces (internal)

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15

These contracts bind every (migrated or new) submodule. Violations fail the
import-lint gate; they are not style suggestions.

## L-01 Controller → Service

- Controllers are thin async adapters: validate input (Pydantic v2 models),
  resolve the principal, call exactly one service method per route action,
  wrap the result in the common response envelope.
- A controller MUST NOT import any `repositories` module, any DB/Redis
  client, or `os.environ`.
- Every route declares `response_model` from `common/responses.py` shapes
  and documents its error codes (drawn from the module's `errors.py`).

## L-02 Service → Repository

- Services own business logic and AuthZ: every mutating/querying method
  checks the caller's authorization itself (never assumes upstream did).
- A service MUST NOT import Motor/Beanie collections, Redis clients, or
  `os.environ`. All persistence goes through injected repository
  interfaces.
- Services receive dependencies via constructor parameters (provided by
  `core/container.py`), never constructed inside method bodies.
- Service methods stamp `created_by`/`updated_by`, enforce active-only
  reads, and raise only the module's own error types.

## L-03 Repository → Database

- Repositories expose neutral async CRUD/query methods over one collection
  (plus the `PlatformRepository` Protocol during migration, with
  memory/Redis/Mongo backends selected by environment).
- A repository MUST NOT encode business rules (no AuthZ, no workflow
  state decisions); filtering beyond caller-supplied criteria is forbidden.
- Collection names come only from `database/constants.py`; models inherit
  `BaseDocument`. See `../data-model.md` Entities 1–2.

## L-04 Module errors

- Each module defines its errors in `errors.py` and exception types in
  `exceptions.py`. Error codes are stable strings namespaced by module
  (e.g. `platform.api_key_revoked`).
- Cross-module code catches by contract (documented codes), never by
  importing another module's exception class for control flow.

## L-05 Dependency-injection surface

- `core/container.py` exposes one typed `provide_*()` per dependency
  (logger, redis client, DB session/client, store, each service and
  repository). HTTP/websocket handlers obtain them via FastAPI `Depends`;
  non-request code receives them as constructor arguments.
- Tests override per seam via `app.dependency_overrides` (no app rebuild).
- Async resources (Redis pool, Mongo client, `TaskRegistry`) are created
  and closed in the application `lifespan` only.
