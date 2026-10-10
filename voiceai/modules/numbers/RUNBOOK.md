# Numbers — Runbook

Phone numbers: registration, agent assignment, and the Talko inbound resolve
lookup. Migrated out of the legacy `platform` bucket in spec 0074.

## Endpoints

| Method | Path | Scope | What it does |
|---|---|---|---|
| POST | `/api/v1/phone-numbers` | `platform:write` | Register a number (201) |
| GET | `/api/v1/phone-numbers` | `platform:read` | List this tenant's numbers |
| GET | `/api/v1/phone-numbers/resolve?number=` | `platform:read` | DID → assigned agent (Talko inbound) |
| POST | `/api/v1/phone-numbers/{id}/assign` | `platform:write` | Route inbound to an agent |
| POST | `/api/v1/phone-numbers/{id}/unassign` | `platform:write` | Stop routing |
| DELETE | `/api/v1/phone-numbers/{id}` | `platform:write` | Tombstone the number |

## How inbound call routing works

Talko owns the carrier leg. On an inbound call it does a DB lookup on its own
DID row and, **on a miss, falls back to this API**:

1. Talko calls `GET /phone-numbers/resolve?number=<dialled DID>`.
2. We normalize the digits and match against stored numbers.
3. Assigned → we return `{agent_id, talko_partner_id?, talko_vendor_config_id?}`.
4. Unknown **or unassigned** → 404, and Talko rejects the call fail-closed.

Both failure cases raise the same error on purpose: the endpoint must not
reveal which DIDs exist.

## The digit normalization contract

`normalize_phone_digits` (in `static_methods.py`) must stay byte-identical in
behaviour to Talko's own normalizer. These all resolve to the same key:

```
+9179…    9179…    91 79-…    09179…    10-digit
```

Plus a last-10 fallback for legacy rows stored without a country prefix. **Do
not "tidy" this function** — a change here silently drops inbound calls. The
test vectors in `test_static_methods.py` are the contract.

## Operational notes

- Deleting a number is a **soft** delete (`is_active=False`). It stops resolving
  immediately, but the row remains for audit.
- `talko_partner_id` / `talko_vendor_config_id` are only overwritten when the
  request supplies them; omitting one keeps the stored value. That is what makes
  single-entry onboarding work.
- Rows written before spec 0074 sit under a `{payload: {...}}` envelope and are
  unwrapped on read. A flattening backfill removes the shape; the fallback goes
  with it.

## Troubleshooting

| Symptom | Check |
|---|---|
| Inbound calls to a known number 404 | Is it assigned? `GET /phone-numbers` and look at `assigned_agent_id`. |
| Resolve 404 but the number exists | Compare normalized digits — the dialled number may include a prefix we do not match. |
| Talko never calls resolve | Talko's own DID row hit, so it never falls back. Check its DB first. |
