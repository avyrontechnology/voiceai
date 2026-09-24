# Spec 0020: Tenancy foundation, phase A — plumbing without behavior (M1a)

## Goal

Introduce the tenancy vocabulary as pure, behavior-free code so M1b (backfill +
`auth → identity` + middleware flip) has typed foundations to build on: frozen
`TenantContext`, ambient `ContextVar` access with fail-loud unset reads, the
system-tenant constant, and a tenant isolation error. Nothing reads or writes
it yet; no request path changes.

## Non-goals

- Phase B (separate turn, same spec): `BaseFields.tenant_id`, tenant-scoped
  repository, tenant middleware, tenancy module, default-tenant backfill,
  `auth → identity` shim, audit-direct-first. None of that lands here.
- No `CachedAgentReader` changes (tenant-keying it is an M1b integration item
  per spec 0018). No ULID change. No UI track.

## Interface contracts

New file `voiceai/common/tenancy.py` (follows `common/logger.py` ContextVar
pattern; exported from `voiceai/common`):

- `SYSTEM_TENANT_ID: Final[str] = "system"` — owner of platform-global rows
  (templates, plans).
- `TenantContext` (frozen dataclass): `tenant_id: str` (required),
  `request_id: str` (required), `workspace_id: str | None = None`,
  `principal_id: str | None = None`, `scopes: frozenset[str] = frozenset()`,
  `plan: str = "default"`. Full blueprint shape now so M1b adds producers,
  not fields. `workspace_id` semantics stay explicitly undecided per 0018
  (carried, not enforced).
- `current_tenant() -> TenantContext` — raises `TenantNotBoundError` (500,
  `ErrorCode.INTERNAL_ERROR`) when unset. Fail-loud, never a default tenant
  leak: silently falling back to `"system"` would mix tenant data.
- `bind_tenant(ctx) -> ContextManager` (token-reset; nesting restores outer),
  plus `reset_tenant()` test helper clearing the var.
- `TenantNotBoundError(AppError)` lives in `common/errors.py` + re-export
  (rule: error classes in errors.py), alongside the existing hierarchy.

## Data model

None. No collections, no migrations, no field changes.

## Security notes

- No auth decisions read the context yet — nothing to bypass. The fail-loud
  (not fallback) read is itself the anti-mixup control, reviewed here.
- No secrets, no PII, no outbound calls. `make sec` scope unchanged.

## Test plan

`tests/arch/common/test_tenancy.py` (offline, no app import beyond `common`):
unset raises with 500 status; bind/read/nested-restore/reset cycle;
frozen-ness; system constant value; scopes default empty; full-shape
construction. Existing suites untouched and green.

## Verification

```sh
make check
make sec
.venv/bin/python -m pytest -q tests/arch/common/test_tenancy.py
```

## Rollout

Single merge, additive-only. No flags (nothing reads the var yet), no
migrations. Rollback is a revert; nothing depends on the new names.

## Burn-down

- [x] M1a: context type + var + error + tests (c41fbb03).
- [ ] M1b slices below, one commit each, `make check` green each.

## M1b decisions (approved)

1. Tenant id values reuse `org_id` verbatim (`"default"` survives as a real
   tenant id). No mapping table.
2. Backfill runs in a brief maintenance window with writers stopped (single
   VM); script stays idempotent so reruns are safe.
3. `auth → identity` is a re-export shim now (`# legacy-shim(spec-0020)`);
   the real file move waits for M9 cutover.
4. Anonymous/public routes bind `SYSTEM_TENANT_ID` with empty scopes, keeping
   `current_tenant()` total behind the middleware.
