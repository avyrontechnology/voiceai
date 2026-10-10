# Spec 0074: phone numbers module (retire the `numbers` platform family)

## Goal

Move the `numbers` family out of the legacy `platform` bucket into a real
module, `voiceai/modules/numbers/`, so the composition root stops serving it
through `platform.store` / `platform.router`.

This is **migration step 1 of the composition-root retirement**. It is
deliberately the smallest of the remaining families: six endpoints, one
collection, no orchestration. Its job is to establish the pattern the other
~11 families (`graphs`, `workflows`, `runs`, `campaigns`, `integrations`,
`subs`, `singletons`, `vector`, `batches`, `kbs`, `webhooks`, `inbound`) will
follow.

## Why this family first

- No cross-family coupling: numbers reads only its own rows.
- The one genuinely subtle behaviour — digit normalization for the Talko
  inbound resolve — is pure, testable logic with no I/O.
- After it, the route table loses its first `platform` entry, which is the
  proof that the retirement path works end to end.

## Non-goals

- Migrating `kbs`/`webhooks`/`graphs`/… (each gets its own spec).
- Removing `platform.store` itself — it still serves the other families. This
  spec only removes *one* consumer; the `PLATFORM_NUMBERS` collection entry and
  the store methods retire when the last consumer is gone.
- Changing the public API. Paths, request bodies, response shapes and status
  codes are byte-for-byte what `numbers_router` serves today.

## Interface contracts

Module `voiceai/modules/numbers/`, Rule 1 file set, wallet as the shape
reference.

### Models (`models.py`)

`PhoneNumber` becomes a `BaseFields` document — that inheritance is what
replaces the legacy `PlatformRow(payload: dict)` envelope, so the typed fields
are finally persisted instead of hidden in an opaque dict.

- `number: str` — the DID as entered (`+9179…`, `9179…`, `91 79-…`).
- `provider: PhoneNumberProvider` — closed vocabulary (`simulated` default).
- `country: str` — ISO-ish, defaults `IN`.
- `assigned_agent_id: str | None` — the routing target; `None` = unassigned.
- `status: str` — `active` default.
- `talko_partner_id: int | None`, `talko_vendor_config_id: str | None` — the
  Talko owner used for single-entry dynamic inbound.

`id` replaces `number_id` as the document key. The API still returns
`number_id`, because the route contract must not change: the controller maps
`id → number_id` on the way out. Internal callers that used `number_id` are
updated in the same spec.

### Service (`service.py`)

- `create(payload) -> PhoneNumber`
- `list_all() -> list[PhoneNumber]`
- `get(number_id) -> PhoneNumber` (raises `PhoneNumberNotFoundError`)
- `assign(number_id, agent_id, talko_*) -> PhoneNumber`
- `unassign(number_id) -> PhoneNumber`
- `delete(number_id) -> None` (raises `PhoneNumberNotFoundError`)
- `resolve(digits) -> ResolvedNumber` — the Talko lookup

All authorization decisions live here (Rule 1e); the controller never decides.

### Static methods (`static_methods.py`)

- `normalize_phone_digits(raw) -> str` — moved **verbatim** from
  `platform/store.py:43`, including the `91`/leading-`0`/10-digit rules. It is
  the contract with Talko's own normalizer; a "cleanup" here is a production
  outage, so the migration copies it and pins it with the same test vectors.
- `resolve_response(record) -> dict` — builds the resolve payload
  (`agent_id`, plus the Talko owner keys only when set).

### Repository (`repository.py`)

`PhoneNumberRepository` over a `BaseRepository[PhoneNumber]`, taking the
collection as a constructor arg. Tenant-scoped, matching every other module
(§3): the numbers a tenant sees are the ones it may resolve.

### Errors (`errors.py`)

`PhoneNumberNotFoundError(AppError)` and `NumberUnassignedError(AppError)` —
the latter so `resolve` can distinguish "no such DID" from "exists but
unassigned" in logs without leaking either to the client (both 404).

## Data model

Collection `platform_numbers` is **kept** (AGENTS.md Rule 5 — the registry is
the single source of collection names, and a rename would orphan live rows).
The document shape changes from `{payload: {...}}` to typed fields.

**Migration note (deferred, not hidden):** existing rows are stored under the
legacy envelope. This spec adds a read-time fallback that unwraps `payload`
when present, so live rows keep resolving without a migration script; a
follow-up backfill flattens them and the fallback is deleted with it. The
fallback is removed in the same spec that runs the backfill — it is not left
as permanent debt.

## Security notes

- Auth stays exactly as it is today: `require_scope("platform:read")` on
  reads/resolve and `platform:write` on mutations. The module does not
  widen access as a side effect of migrating.
- `resolve` is the inbound-routing endpoint. It returns only the assigned agent
  id and Talko owner ids — never the DID itself, and never a payload. An
  unassigned DID is a 404 with no hint that the row exists (fail-closed, as the
  Talko integration requires).
- The acting user comes from auth context; `assigned_agent_id` is a routing
  target the caller names deliberately, and is recorded in `updated_by`.
- Phone numbers are PII: they are never logged at INFO. Errors log the
  `number_id`, never the digits.

## Architecture delta

```
NEW  voiceai/modules/numbers/{constants,models,errors,repository,service,
                              controller,static_methods,schemas}.py
NEW  voiceai/database/constants.py — PHONE_NUMBERS alias documented as the
     canonical name for the collection that legacy calls platform_numbers
MOD  voiceai/core/container.py — numbers_service provider (Factory)
MOD  voiceai/core/app_factory.py — numbers_router mounted from the module
MOD  voiceai/platform/router.py — numbers_router removed from single_app_routers
DEL  voiceai/platform/models.py — PhoneNumber family models
DEL  voiceai/platform/store.py — save/get/list/delete_number, get_number_by_digits
```

`single_app_routers()` returns one fewer router; `tests/arch/test_route_inventory.py`
is updated in the same spec since it pins that table.

## Test plan

- `static_methods`: normalization vectors — `+9179…`, `9179…`, `91 79-…`,
  `09179…`, 10-digit, empty, non-Indian fallback. The Talko contract.
- `service`: create/list/get/assign/unassign/delete; assign overwrites the
  Talko owner only when provided (omitted = keep, per the current behavior);
  delete on a missing id raises.
- `repository`: tenant isolation (another tenant's number is not visible), the
  legacy-`payload` read fallback, and soft delete.
- `controller`: all six routes through the real app factory with `httpx`
  (create 201, list 200, resolve 200 + 404, assign/unassign 200, delete 200 +
  404), plus the scope gate.
- Resolution: the exact request/response bodies the legacy router returned, so
  Talko sees no change.

Coverage ≥85% on the new module.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

The module router mounts **before** the platform routers and first match wins,
which is how the module takes the path. Cutover is therefore a single commit;
rollback is reverting it. No feature flag: the endpoint surface is identical,
so a flag would only add a way to be wrong.

## Burn-down

- [ ] `numbers/constants.py`, `models.py`, `errors.py`
- [ ] `numbers/static_methods.py` — normalization + resolve payload
- [ ] `numbers/repository.py`
- [ ] `numbers/service.py`
- [ ] `numbers/schemas.py` + `controller.py` (six routes)
- [ ] container provider + app_factory mount
- [ ] `platform/router.py`: numbers_router removed
- [ ] `platform/store.py` + `platform/models.py`: numbers family deleted
- [ ] `test_route_inventory.py` updated
- [ ] tests: static methods, service, repository, controller
- [ ] `make check` + `make sec` + `make cov` green
