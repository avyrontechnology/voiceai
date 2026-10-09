# Spec 0072: data subject rights (access, rectification, erasure, portability)

## Goal

GDPR Chapter 3 gives a data subject four rights against every system holding
their data. Extend the `privacy` module with a subject-facing surface that
answers all four over the tenant's data:

- **Art. 15 access** — `GET /privacy/subject/{subject_id}/data` returns every
  record the tenant holds for the subject, grouped by collection.
- **Art. 16 rectification** — `PATCH /privacy/subject/{subject_id}` updates
  caller-supplied fields on subject-linked records.
- **Art. 17 erasure** — `DELETE /privacy/subject/{subject_id}` soft-deletes
  every subject-linked record (tombstones preserved — deletion is recorded,
  never destructive).
- **Art. 20 portability** — the same data in machine-readable JSON with a
  stable schema, downloadable.

## Non-goals

- Retention jobs (spec 0073) and the DSAR workflow (0074) build on this.
- Consent records themselves are NOT erasable by the subject surface: consent
  withdrawal is the tombstone path already shipped (spec 0071) and the row is
  audit evidence.
- UI download flows — API only.

## Interface contracts

Owning module `voiceai/modules/privacy`, additive only.

- `GET /api/v1/privacy/subject/{subject_id}/data` (`platform:read`): 200
  `{consent: [...], exported_at, subject_id}` — every active consent row the
  tenant holds for the subject. Collections beyond consent join as Phase 2
  specs land them.
- `DELETE /api/v1/privacy/subject/{subject_id}` (`platform:write`): 200
  `{erased: true, records: n}` — soft-deletes every active consent row for
  the subject. Idempotent: a second call reports `records: 0`.
- `PATCH /api/v1/privacy/subject/{subject_id}` (`platform:write`): 200 —
  re-grants/updates consent purposes per body `{purpose, policy_version}`.
  Unknown purpose → 400. Opaque errors throughout (no oracle).

Erasure writes an audit row through the privacy service (identifiers only),
so the act of erasure is itself provable.

## Data model

New collection `PRIVACY_AUDIT_LOG = "privacy_audit_log"`.
`PrivacyAuditRecord(BaseFields)`: `subject_id`, `action`
(`erasure|rectification|access`), `detail` (identifiers only, e.g. affected
purpose list), `actor_id` (acting principal, never the subject).

## Security notes

- Authorization in the service layer; `subject_id` is a lookup key, never an
  authorization grant (the tenant boundary comes from the scoped repository).
- Erasure is soft: rows keep `is_active=False`; hard deletion contradicts
  auditability and is out of scope.
- Audit rows carry identifiers only; `redact_secrets` applies to any log.
- `make sec` clean; no new dep.

## Test plan

Unit (service/repository with DI fakes) + controller through the real app
factory, offline. Matrix: access returns active rows; access empty subject →
200 `[]`; erasure soft-deletes + writes audit; erasure idempotent; rectification
re-grant happy; rectification unknown purpose 400; audit row carries actor.
Coverage ≥85% touched.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Additive (1 module + 1 collection). No migration. Rollback = revert.

## Burn-down

- [ ] `database/constants.py`: `PRIVACY_AUDIT_LOG`
- [ ] `privacy/models.py`: `PrivacyAuditRecord`
- [ ] `privacy/service.py`: `access_data`, `erase_subject`, `rectify_subject` + audit writer
- [ ] `privacy/controller.py`: GET/DELETE/PATCH `/privacy/subject/...`
- [ ] `privacy/schemas.py`: subject payloads/views
- [ ] `privacy/CONTRACT.md`: routes + semantics
- [ ] Unit + controller tests (offline)
- [ ] Arch pins (route inventory, tenant isolation, census, model shape)
- [ ] `make check` + `make sec` + `make cov` green
