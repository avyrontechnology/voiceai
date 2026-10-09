# privacy CONTRACT

Owner: `squad-platform`.

Consent records for GDPR Article 7 demonstrability (spec 0071): who
consented, to what, under which policy version, and when. Subject ids are
opaque (a hash or user id — never a raw email); caller IPs persist only as
SHA-256 digests (`utils.caller_ip_hash`), never raw.

## Routes

Mounted under `/api/v1` by the app factory. Every route is scope-gated:
anonymous callers answer the 401 envelope, under-scoped principals the 403
envelope, and the gate runs before any service call.

| Route | Scope | Answers |
| --- | --- | --- |
| `POST /api/v1/privacy/consent` | `platform:write` | 201 view; 400 unknown purpose; 422 bad body |
| `GET /api/v1/privacy/consent/{subject_id}` | `platform:read` | 200 active views (`[]` for an unknown subject) |
| `DELETE /api/v1/privacy/consent/{subject_id}/{purpose}` | `platform:write` | 200 withdrawn view; 400 unknown purpose; 404 unknown pair |

- Body (`POST`): `subject_id`, `purpose`, `policy_version`; unknown keys
  fail request validation (422 envelope).
- Withdrawal is a tombstone (`granted=False`, `withdrawn_at` set), not a
  delete: the fact of withdrawal is itself audit evidence. Reads exclude
  tombstones, so clients never see a withdrawn grant.
- `purpose` is a plain string validated in the service against the closed
  vocabulary in `constants.py` (`static_methods.is_known_purpose`), so an
  unknown purpose answers the module's 400, not a generic 422.

## Events

- in: none.
- out: none (grants are same-call writes; logging is identifiers-only).

## Collections

- `consent_records` (`Collections.CONSENT_RECORDS`): `ConsentRecord`
  rows, `tenant_id`-scoped; tombstones retained as audit evidence.

## Data subject rights (spec 0072)

| Route | Scope | Contract |
|---|---|---|
| `GET /api/v1/privacy/subject/{subject_id}/data` | `platform:read` | 200 `{subject_id, consent:[...]}` — every active row (Art. 15); records the access in the audit log |
| `DELETE /api/v1/privacy/subject/{subject_id}` | `platform:write` | 200 `{erased: true, records: n}` — soft-erases every active row (Art. 17); idempotent |
| `PATCH /api/v1/privacy/subject/{subject_id}` | `platform:write` | 200 consent view — re-stamps `policy_version` on the active row (Art. 16) |

- Every subject-rights action writes a `PrivacyAuditRecord`
  (`privacy_audit_log`): `subject_id`, `action`
  (`access|rectification|erasure`), `actor_id`, identifiers-only detail.
- Erasure is soft by contract; tombstone rows stay as audit evidence.
