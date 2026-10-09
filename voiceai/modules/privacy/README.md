# privacy

GDPR consent records (spec 0071): grant, list, and withdraw consent per
subject and purpose. Owned by `squad-platform`; the binding surface is
`CONTRACT.md`.

- Routes: `/api/v1/privacy/consent` (grant), `.../{subject_id}` (list
  active), `.../{subject_id}/{purpose}` (withdraw), all scope-gated —
  `platform:write` to grant/withdraw, `platform:read` to list.
- Withdrawals tombstone (`granted=False`) rather than delete — the fact of
  withdrawal is audit evidence; reads serve active rows only.
- Subject ids are opaque and caller IPs persist only as SHA-256 digests:
  the module stores identifiers, never raw PII.
- Purposes are a closed vocabulary in `constants.py`; an unknown purpose
  answers the module's 400 envelope.
