# numbers

Phone numbers for the platform: registering DIDs, assigning them to agents, and
resolving an inbound call to the right agent. Migrated out of the legacy
`platform` bucket in spec 0074, where the family was an opaque
`{payload: ...}` row behind a `MemoryStore`.

Read [CONTRACT.md](CONTRACT.md) for the routes and invariants,
[RUNBOOK.md](RUNBOOK.md) for operations and troubleshooting.

## Layout

| File | Role |
|---|---|
| `constants.py` | Every literal: routes, scopes, defaults, normalization lengths |
| `models.py` | `PhoneNumber` — a `BaseFields` document |
| `schemas.py` | Request/response bodies mirroring the legacy API exactly |
| `errors.py` | `PhoneNumberNotFoundError`, `NumberUnassignedError` |
| `exceptions.py` | Guard helpers that raise those errors |
| `repository.py` | Storage, including the legacy-envelope read fallback |
| `service.py` | Business logic and authorization outcomes |
| `helpers.py` | Small mapping helpers |
| `static_methods.py` | Digit normalization and the resolve payload (pure) |

## The one thing to read before touching this module

`static_methods.normalize_phone_digits` is a contract with **Talko**, an external
system. Both sides must map `+9179…`, `9179…`, `91 79-…` and 10-digit variants
to the same key, or inbound calls stop routing — silently. The function was
moved verbatim from the legacy store; the test vectors pin its behaviour. See
`RUNBOOK.md`.

## Tenant scoping

Two deliberately different wirings:

- The **API** reads a per-request scoped repository — a tenant sees only its own
  numbers.
- The **voice inbound seam** reads an *unscoped* repository. A carrier webhook is
  unauthenticated and arrives before any tenant is known, so the DID→agent lookup
  must see every tenant's rows.
