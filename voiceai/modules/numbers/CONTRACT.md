# numbers CONTRACT

Owner: `squad-voice` (`#squad-voice`).

## Routes (mounted under `/api/v1`)

- `POST /phone-numbers` — register a number, 201 (scope `platform:write`).
- `GET /phone-numbers` — list this tenant's numbers (scope `platform:read`).
- `GET /phone-numbers/resolve?number=` — DID → assigned agent, the Talko
  inbound lookup (scope `platform:read`).
- `POST /phone-numbers/{id}/assign` — route inbound to an agent (scope
  `platform:write`).
- `POST /phone-numbers/{id}/unassign` — stop routing (scope `platform:write`).
- `DELETE /phone-numbers/{id}` — tombstone the number (scope `platform:write`).

Errors are `PhoneNumberNotFoundError` / `NumberUnassignedError` (both 404).

## Events

- in: none. out: none.

## Collections

- `platform_numbers` (`Collections.PLATFORM_NUMBERS`) — `PhoneNumber` documents.
  The row name predates this module and is kept so live rows are not orphaned
  (spec 0074).

## Invariants

- **The API is unchanged from the legacy `platform` router.** Same paths, bodies,
  response shapes and status codes. `number_id` in the response is the document
  `id`; the field name is legacy and stays for client compatibility.
- **Resolve fails closed.** An unknown DID and an unassigned DID raise the same
  error, so the endpoint never reveals which numbers exist.
- **Delete is soft** (`is_active=False`): the number stops resolving immediately
  and the row survives for audit.
- **Assign keeps an omitted Talko owner.** `talko_partner_id` /
  `talko_vendor_config_id` are overwritten only when the request supplies them,
  which is what makes single-entry Talko onboarding work.
- **Normalisation is a contract with Talko.** `normalize_phone_digits` must stay
  behaviourally identical to Talko's normalizer; see `RUNBOOK.md`.

## Ports

- Consumes `PhoneNumberRepository` (tenant-scoped for the API, unscoped for the
  voice inbound seam — the webhook arrives before a tenant is known).
- Provides `NumbersInboundStore` to `voiceai.modules.voice`, satisfying
  `InboundLookupStore`. That store is tenant-blind and read-only by contract.

## Layering

`controller → service → repository`. The controller holds no storage call and
no routing decision; the service owns authorization outcomes and raises the
module errors; the repository is the only layer that touches the driver.

## Out of scope

The `inbound` family (per-agent inbound config) still lives in the legacy
platform store — `NumbersInboundStore` reads it until that family migrates.
