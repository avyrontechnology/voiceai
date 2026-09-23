# Spec NNNN: <slug>

> Copy this file to `specs/NNNN-slug.md` and fill every section. Delete nothing —
> write "N/A" with a reason where a section does not apply.

## Goal

One paragraph: what changes for whom, and why now.

## Non-goals

Explicitly out of scope (prevents scope creep during implementation).

## Interface contracts

Routes / events / public functions added or changed, with request/response shapes.
Name the owning module and the exact files touched (disjoint sets per agent).

## Data model

Collections, fields, indexes, migrations/backfill. Every persisted model inherits
`database.base.BaseFields`; collection names come from `database.constants.Collections`.
Deletes are soft (`is_active=False`) unless argued here.

## Security notes

AuthN/Z decisions (service layer), PII handling (identifiers at INFO, never payloads),
secrets (env only, `redact_secrets` before logging), SSRF (`is_safe_outbound_url` +
timeout), rate limits / idempotency for mutating endpoints, CORS impact.

## Test plan

Unit (service/repository/static_methods with DI fakes) + controller tests through the
real app factory (`httpx` ASGI transport), offline only. New behavior without a test
does not exist. Coverage target ≥ 85% on touched modules.

## Verification

Commands the integrator runs (default):

```sh
make check
make sec
```

Plus any spec-specific checks (e.g. `make cov`, load test, provider golden suite).

## Rollout

Flag-gated? Dual-mount? Migration steps? Rollback plan? Shim burn-down entries
(`# legacy-shim(spec-NNNN)` files registered here, deleted at cutover).

## Burn-down

- [ ] …
