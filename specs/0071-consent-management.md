# Spec 0071: consent management (GDPR Phase 2)

## Goal

GDPR Article 7 requires demonstrable consent: who consented, to what purpose,
when, and which policy version. New `privacy` module owns consent records —
`POST /privacy/consent` to grant, `GET /privacy/consent/{subject_id}` to read,
`DELETE /privacy/consent/{subject_id}/{purpose}` to withdraw. Withdrawal is
a tombstone, not a delete (the fact of withdrawal is itself audit evidence).

## Non-goals

- DSAR export (spec 0074), erasure (spec 0072), retention jobs (spec 0073)
- Consent UI — API only in this spec
- Marketing preference center — purposes are API-defined

## Interface contracts

Owning module `voiceai/modules/privacy` (new, `squad-platform`), following the
health-module file set: `constants.py`, `models.py`, `errors.py`,
`exceptions.py`, `repository.py`, `service.py`, `controller.py`, `schemas.py`.

- `POST /privacy/consent` (`platform:write`): body `{subject_id, purpose,
  policy_version}` → 201 consent record. `purpose` must be a known purpose
  constant; `subject_id` is an opaque caller identifier (email hash, user id —
  never stored raw when it is an email).
- `GET /privacy/consent/{subject_id}` (`platform:read`): → 200 list of active
  consents for the subject (withdrawn excluded).
- `DELETE /privacy/consent/{subject_id}/{purpose}` (`platform:write`): →
  200 tombstone (granted=False, withdrawn_at set). Unknown pair → 404.

Purposes (constants): `analytics`, `marketing`, `third_party_sharing`,
`voice_recording`. New purposes arrive via constants + spec, never free text.

## Data model

New collection `CONSENT_RECORDS = "consent_records"`.
`ConsentRecord(BaseFields)`: `subject_id` (opaque, indexed), `purpose`,
`granted: bool`, `policy_version`, `granted_at`, `withdrawn_at: datetime | None`,
`ip_hash: str | None` (caller IP hashed, never raw). Tenant-scoped via
`TenantScopedRepository`. Soft deletes only.

## Security notes

- Acting tenant from auth context; `subject_id` is caller-supplied identity for
  lookup only, never trusted for authorization — authorization stays in the
  service layer via scope check.
- `ip_hash` is SHA-256 (not reversible); raw IPs never persisted.
- Error opacity: unknown subject/purpose → same 404 shape, no oracle.
- `make sec` clean; no new dep.

## Test plan

Unit (service/repository with DI fakes) + controller through real app factory
(`httpx` ASGI), offline only. Matrix: grant happy, duplicate grant (idempotent
re-grant), unknown purpose 400, withdraw happy, withdraw unknown 404,
withdrawn excluded from reads. Coverage ≥85% touched.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Additive (1 module + 1 collection). No migration. Rollback = revert + drop
collection. UI consent center consumes same routes later.

## Burn-down

- [ ] `database/constants.py`: `CONSENT_RECORDS`
- [ ] `modules/privacy/` package (constants, models, errors, exceptions, repository, service, controller, schemas, `__init__` + registry entry)
- [ ] Container wiring + router mount
- [ ] Unit + controller tests (offline)
- [ ] `make check` + `make sec` + `make cov` green
