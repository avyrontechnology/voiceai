# database

Persistence primitives every module shares (AGENTS.md rule 5): the audit envelope
every persisted model inherits, the single registry of collection names and driver
literals, the repository protocol with its two backends, and the tenant-isolation
choke point. Modules never touch a driver — a module `repository.py` takes a
`BaseRepository[Model]` by constructor (the container picks the backend, rule 9)
and speaks in module models only (rule 1d).

## Files

- `base.py` — `BaseFields`: `id`, `tenant_id`, `created_*`/`updated_*`, `is_active`, `meta`.
- `constants.py` — `Collections` (the only place a collection name is spelled),
  repository error text, `TENANT_ID_FIELD`, the driver field names and
  `LISTING_SORT` every selector is built from, Mongo timeouts.
- `repository.py` — `BaseRepository` (structural protocol), `InMemoryRepository`
  (spec 0001), `MotorRepository` (spec 0003).
- `scoped.py` — `TenantScopedRepository`: the one wrapper through which every
  tenant-visible read and write flows (spec 0020).

## Read shapes (spec 0050)

| Need | Member | Bound | Total | Use it for |
| --- | --- | --- | --- | --- |
| one row by id | `get` | 1 | — | detail routes, ownership checks |
| one row by natural key | `find_one` | 1 | — | session hash, API-key hash, slug |
| a handful of rows by key | `find_many` | `MAX_PAGE_SIZE` | no | an agent's chat sessions, small fan-out |
| every row, paged | `list` / `list_where` | `page_size` per page | yes | list routes, page-walkers |

`find_many` is a **bounded lookup**: it clamps to `MAX_PAGE_SIZE` and carries no
total, so it can never tell you whether more rows exist. Anything that must see
every match walks `list_where` until `has_next` is false — never a lookup
windowed in Python (that is the bug spec 0050 removed from the scoped listing),
and never a single `list(page=1, page_size=MAX_PAGE_SIZE)` read, which caps at
100 rows just the same.

The loop is written once, in `repository.walk_pages`:

```python
from voiceai.database.repository import walk_pages

rows = [row async for row in walk_pages(self._store)]                     # every row
hooks = [row async for row in walk_pages(self._store, {KIND_FIELD: "webhook"})]
```

It works over any backend or scoped view, is lazy (break early and later pages are
never fetched), and stops on the first empty page so a misbehaving backend cannot
spin it. Route handlers that expose paging to clients keep threading the request's
`PaginationParams` through to `list`/`list_where` and returning the `Page`.

## Tenant scoping

Wrap any backend in `TenantScopedRepository(inner, tenant_id, collection)`.
Reads return only rows stamped with the bound tenant (pre-tenancy `None` rows are
invisible), listings stamp the tenant into the selector the backend pages, and
writes stamp it and verify ownership first, failing with the generic not-found
error (no existence oracle). The unscoped exception is `system_scope`, whose call
sites the tenant-isolation sentinel allowlists.

See `CONTRACT.md` for the exact member semantics and backend divergences. Tests:
`tests/arch/database/`. Cross-cutting package (no module registry entry); changes
land through a spec like any other (0001, 0003, 0020, 0050).
