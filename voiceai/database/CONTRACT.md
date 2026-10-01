# database CONTRACT

The `BaseRepository[TModel]` protocol (`repository.py`) and the guarantees every
backend and the scoped wrapper honour. `TModel` is any `BaseFields` subclass.

## Members

| Member | Returns | Contract |
| --- | --- | --- |
| `insert(model)` | stored copy with `id` | Assigns a uuid4 hex id when missing. A model that carries an id **replaces** the stored document, even a soft-deleted one — the only resurrection path. Caller's instance is never mutated. |
| `get(item_id)` | `TModel \| None` | Active document by id; unknown and soft-deleted both read as `None`. |
| `list(params)` | `Page[TModel]` | Unfiltered paged listing, oldest first; identical to `list_where({}, params)`. |
| `list_where(filters, params)` | `Page[TModel]` | Paged listing of the active documents matching every `{field: value}` equality filter (ANDed), oldest first; `total` counts every active match. The backend applies the filter and the window — every match is reachable by walking pages. `is_active` cannot be widened through `filters`. (spec 0050) |
| `update(model)` | stored copy, `updated_at` bumped | Replaces an **active** document; raises `NotFoundError` when missing or soft-deleted (never resurrects). |
| `soft_delete(item_id, *, user_id=None)` | `bool` | Flags `is_active=False`, stamps `updated_by` when an actor is given; `True` only for the call that did it (idempotent). |
| `find_one(field, value)` | `TModel \| None` | First active document where `field == value`. |
| `find_many(field, value, *, limit=MAX_PAGE_SIZE)` | `Sequence[TModel]` | **Bounded lookup**, not a listing: at most `min(limit, MAX_PAGE_SIZE)` active matches, oldest first, no total. |

Rules shared by every member:

- Soft delete is the only delete (rule 5): no read surfaces `is_active=False`.
- Field names in `filters`/`field` are code constants at the call site, never user
  input; values are BSON scalars. Selectors are driver-native dicts — no query
  strings are ever concatenated.
- The one repository error is `NotFoundError(DOCUMENT_NOT_FOUND_MESSAGE)` with
  `collection`/`item_id` details; nothing driver-specific leaks into the envelope.

## Page-walker

`walk_pages(repository, filters=None) -> AsyncIterator[TModel]` (free function in
`repository.py`, not a protocol member — fakes need nothing new) yields every active
match by calling `list_where(filters, PaginationParams(page=n, page_size=MAX_PAGE_SIZE))`
from `MIN_PAGE` upward. It stops at the first page that is empty **or** reports
`has_next == False`; the empty-page stop is the loop guard against a backend whose
`total` counts rows the scoped view drops. Order is the backend's listing order.
Offset paging is not a snapshot: a concurrent insert or delete can shift a page
boundary, so one row may repeat or go missing at the seam. Every read that must see
every row uses this walker; a route that exposes paging returns the `Page` instead.

## Ordering and totals

- `InMemoryRepository`: insertion order.
- `MotorRepository`: `LISTING_SORT` = `created_at` ascending, `_id` ascending as
  the tiebreak (uuid-hex ids carry no time order). Same-instant rows may swap
  relative to the in-memory order; nothing else diverges.
- `Page.total` is exact for the selector paged (`count_documents` on Motor, the
  matched length in memory); `page_size` is clamped to `MAX_PAGE_SIZE` by
  `PaginationParams`, page numbers are unbounded.

## Motor document shape

Stored document = `model.model_dump(exclude={"id"})` plus `_id = id` **as a string**
(never `ObjectId`); reads map `_id` back to `id` before validation. Selectors are
built from `constants.py` (`MONGO_ID_FIELD`, `IS_ACTIVE_FIELD`, `CREATED_AT_FIELD`,
`UPDATED_*_FIELD`, `MONGO_SET_OPERATOR`) — no inline literals. Writes are single
driver operations (upsert, guarded replace, guarded `$set`) so the contract holds
atomically where the in-memory backend reads-then-writes.

The model `id` is not a filter key on Motor: it is stored as `_id`, so
`find_one`/`find_many`/`list_where` keyed by `id` match nothing there while the
in-memory backend matches the attribute. Read by id with `get`.

## `TenantScopedRepository` (structural subtype of the protocol)

Construction with an empty tenant raises `TenantNotBoundError`.

| Member | Scoped behaviour |
| --- | --- |
| `insert` / `update` | Stamp the bound tenant into a copy; `update`/`soft_delete` first verify the stored row belongs to the tenant and raise the generic `NotFoundError` otherwise (missing, deleted and foreign look identical). |
| `get` | `None` unless the row is active and stamped with the bound tenant (pre-tenancy `None` rows are invisible). |
| `list(params)` | `list_where({}, params)`. |
| `list_where(filters, params)` | A `tenant_id` filter naming anything but the bound tenant (another tenant or `None`) returns an empty page **without touching the backend**. Otherwise the bound tenant is added to `filters` and the backend pages that selector; returned rows are re-checked for tenant and `is_active` (defense in depth), `total` is the backend's count. One driver query per page, every row reachable. (spec 0050) |
| `find_one` / `find_many` | Same short-circuit for a foreign tenant filter; otherwise the inner lookup runs and rows are filtered to the bound tenant in-process. `find_many` stays bounded **before** that filter — on a field shared across tenants fewer than `limit` own rows may come back even when more exist; a read that must see every match uses `list_where`. |
| `system_scope(inner)` | The audited unscoped exception for tenant discovery; call sites are pinned by `tests/arch/test_tenant_isolation.py::SYSTEM_SCOPE_FLAGGED`. |

## Fakes

A structural fake must implement every member above, including `list_where`,
or `make type` fails at the injection site. Reference fake:
`tests/arch/database/test_scoped.py::_FakeRepository`.

## Specs

0001 (protocol, in-memory), 0003 (Motor), 0020 (tenant scoping), 0050 (paged
and filtered listing, `walk_pages`; `find_many` re-scoped as a bounded lookup).
