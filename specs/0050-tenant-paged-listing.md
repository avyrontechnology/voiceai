# Spec 0050: tenant-paged listing

> Bug class: silent data loss on every tenant listing past 100 rows. Found while
> auditing spec 0048's single-app cutover; fixed at the one choke point
> (`database/scoped.py`) plus the protocol it wraps.

## Goal

`TenantScopedRepository.list()` serves a page by calling
`inner.find_many(TENANT_ID_FIELD, tenant, limit=MAX_PAGE_SIZE)` and windowing that
list in Python. `find_many` is bounded at `MAX_PAGE_SIZE` (100) by design in both
backends, so a tenant with more than 100 active rows in any collection silently
loses the rest from every module listing that goes through the scoped view —
agents, executions (`PlacedCall`), tools, voices, chat sessions, the wallet ledger:
`list(page=2)` is empty and `total` reports 100. Module code that walks pages until
`has_next` is false (`agents.repository.list_agents`, `auth.repository`'s walkers,
`wallet` templates, `platform/repository_store._all`) stops after the first page
believing it saw everything. Call sites that read only page 1 with
`page_size=MAX_PAGE_SIZE` (`tools.repository.list_all`, `voices.repository.list_voices`,
`wallet.list_ledger`) get a correct `total` from this spec but still see at most 100
rows until they walk pages — a per-module follow-up (see *Burn-down*).

After this spec the repository protocol has a real **paged-and-filtered read**,
`list_where(filters, params)`, served at the driver level (`find` + `count_documents`
with `skip`/`limit` on Motor, filter-then-window in memory), and the scoped `list`
is a thin, fail-closed wrapper over it. `find_many` keeps its bound and is documented
as what it is: a bounded lookup, not a listing.

## Non-goals

- Changing any module's call sites. Modules keep calling `scoped.list(params)`;
  page-walkers get every page now, and page-1-only readers adopt `walk_pages` (below)
  under their own specs. Adopting `list_where` for module-level filtered listings
  (e.g. `wallet.repository.list_ledger`'s in-process `entry_type` filter) is likewise
  a per-module follow-up, not this spec.
- Touching `voiceai/platform/repository_store.py` (`_all` pages the unscoped
  repository and filters in-process by design until M6 retires the bridge).
- Cursor/keyset pagination, sort options, range or `$in` filters. `list_where` is
  equality-only on scalar values.
- Index creation. The `scripts/` tree that owns migrations is outside this file set;
  the recommended index is recorded under *Data model* for that follow-up.
- Removing `find_many`. Chat still uses it as an `agent_id` lookup.

## Interface contracts

Owning package: `voiceai/database/` (files: `repository.py`, `scoped.py`,
`constants.py`, `README.md` (new), `CONTRACT.md` (new)); tests in
`tests/arch/database/`.

### `BaseRepository` protocol — new member

```python
async def list_where(self, filters: Mapping[str, Any], params: PaginationParams) -> Page[TModel]:
```

- Returns one page of **active** documents where every `filters[field] == value`,
  oldest first (`created_at`, then id as tiebreak on Motor; insertion order in
  memory), with `total` counting every active match across all pages.
- `filters` keys are code constants, never user input; values are BSON scalars.
- An empty mapping is the unfiltered listing: `list(params)` ≡ `list_where({}, params)`
  in every backend (the implementations delegate).
- The active-only guard always wins: a caller cannot pass `is_active=False` to
  read soft-deleted rows through this member (rule 5).

### `find_many` — unchanged signature, clarified contract

Bounded equality lookup (at most `MAX_PAGE_SIZE` rows, oldest first). It is not a
listing: callers that need every match page with `list_where`.

### `walk_pages` — the one page-walker (free function, `repository.py`)

```python
async def walk_pages(repository: BaseRepository[TModel], filters: Mapping[str, Any] | None = None) -> AsyncIterator[TModel]:
```

- Yields every active match of `filters` (`None`/empty = unfiltered) by calling
  `repository.list_where(filters, PaginationParams(page=n, page_size=MAX_PAGE_SIZE))`
  from `MIN_PAGE` upward until a page is empty or reports `has_next == False`.
- Works over any protocol implementation, scoped or not (a scoped view stamps the
  tenant into the selector per its own contract); it is not a protocol member, so
  fakes need nothing new.
- Lazy: a consumer that breaks out of the iteration never fetches later pages.
- The empty-page stop is the loop guard: a backend whose `total` counts rows the
  scoped view drops (the defensive re-filter) cannot spin the walker forever.
- Replaces the four hand-written `has_next` loops (`agents.list_agents`,
  `auth.repository._all`, `wallet.list_templates`, `platform/repository_store._rows`)
  and is the one-line fix for the page-1-only readers listed under *Burn-down*:
  `rows = [row async for row in walk_pages(self._store)]`.

### `InMemoryRepository` / `MotorRepository`

- `list` delegates to `list_where({}, params)`.
- Memory: filter `_active_models()` by every pair, then window by
  `params.skip`/`params.limit`; `total` is the matched count.
- Motor: selector `{**filters, "is_active": True}`; `count_documents(selector)` for
  `total`; `find(selector).sort(created_at, _id).skip(params.skip).limit(params.limit)`.
- Driver field names and operators (`id`/`_id`, `is_active`, `created_at`,
  `updated_at`, `updated_by`, `$set`) and the listing sort spec move to
  `database/constants.py` (`MODEL_ID_FIELD`, `MONGO_ID_FIELD`, `IS_ACTIVE_FIELD`,
  `CREATED_AT_FIELD`, `UPDATED_AT_FIELD`, `UPDATED_BY_FIELD`, `MONGO_SET_OPERATOR`,
  `SORT_ASCENDING`, `LISTING_SORT`); no inline literals remain in the Motor
  selectors or mutations.

### `TenantScopedRepository`

- `list(params)` → `list_where({}, params)`.
- `list_where(filters, params)`: if `filters` names `tenant_id` with any value
  other than the bound tenant (including `None`), return an empty page without
  consulting the backend (no existence oracle, pre-tenancy rows invisible).
  Otherwise call `inner.list_where({**filters, tenant_id: bound}, params)` and
  re-filter the returned items by `is_active` and tenant **defensively**; `total`
  is the backend's count. Every page is served by one driver query — no Python
  windowing over a bounded lookup.
- `find_many` keeps its bounded-lookup semantics and in-process tenant filter.

### Fakes

Every structural implementation of the protocol grows `list_where`. Audit at spec
time: `tests/arch/database/test_scoped.py::_FakeRepository` is the only fake
(`grep -rn "async def find_many" voiceai tests`); module tests inject the real
`InMemoryRepository`.

## Data model

No new collections or fields. Motor serves `list_where` with
`{**filters, is_active: true}` sorted by `(created_at asc, _id asc)`.

Index recommendation for the migration tool (follow-up, outside this file set):
every tenant-scoped collection wants a compound index
`{tenant_id: 1, created_at: 1, _id: 1}` so the tenant listing is an index range
scan and `count_documents` on the same selector is covered. Without it the query
is still correct — Motor filters and sorts server-side — only slower on large
collections.

## Security notes

- Tenant isolation stays fail-closed: the scoped view stamps the bound tenant into
  every selector it sends and rejects foreign/`None` tenant filters before any I/O;
  returned rows are re-checked against the bound tenant (defense in depth against
  a misbehaving backend). Totals never count another tenant's rows because the
  backend counts the tenant-stamped selector.
- Filter keys are code constants at the call site (same rule as `find_one`/
  `find_many`); values are scalars. Passing a mapping as a value would reach the
  driver as an operator document (`{"$ne": ...}`) — the contract forbids it and the
  boundary pydantic models guarantee scalar path/query parameters. No query strings
  are concatenated: selectors are driver-native dicts.
- No error text changes: `NotFoundError` with `DOCUMENT_NOT_FOUND_MESSAGE` remains
  the only repository error; `list_where` raises nothing of its own.
- Denial of service: `page_size` is already clamped to `MAX_PAGE_SIZE` by
  `PaginationParams`; `skip` grows with page number, which is the existing
  `list` behaviour (a deep page is one bounded driver query, not a scan in Python).
- Logging: none added (repositories do not log payloads).

## Test plan

`tests/arch/database/` (offline, DI fakes per rule 10):

1. **Proof of the bug (red first):** 150 rows for one tenant in an
   `InMemoryDatabase`-backed `TenantScopedRepository`; before the fix
   `list(page=2, size=100)` is empty and `total == 100`; after the fix page 1 has
   100, page 2 has 50, `total == 150`, and the union of ids over pages is every
   row (`test_tenant_paging.py`).
2. Tenant isolation across pages: acme and globex rows interleaved on insert;
   every acme page contains acme rows only, totals count acme alone; the globex
   view sees exactly its own rows.
3. Soft-deleted rows drop from scoped pages and totals.
4. `list_where` by a business field through the scoped view is tenant-scoped at
   the driver (fake records the selector it received).
5. Scoped `list_where` with a foreign or `None` tenant filter returns an empty
   page without touching the backend.
6. `InMemoryRepository.list_where`: filters and windows; `{}` equals `list`;
   `is_active=False` filter yields nothing.
7. `MotorRepository.list_where` against the recording fake collection: selector
   `{field: value, is_active: True}` sent to both `find` and `count_documents`,
   sort/skip/limit recorded, totals and windows correct; scoped-over-Motor pages
   150 rows across two pages.
8. Protocol conformance: `InMemoryRepository`, `MotorRepository` and the scoped
   view all still type-check as `BaseRepository[T]` (existing fixtures).
9. `find_many` is a bounded lookup in both real backends: `limit` above
   `MAX_PAGE_SIZE` clamps to it (Motor sends `limit(MAX_PAGE_SIZE)`), a negative
   `limit` yields nothing, and `list_where` reaches the rows past the bound.
10. `walk_pages`: over the scoped in-memory backend it yields all 153 tenant rows in
    listing order and no foreign row, and a filtered walk yields exactly the matches
    (`test_tenant_paging.py`); over the recording fake it sends one tenant-stamped
    selector per page and stops when `has_next` is false (101 rows → 2 queries), and
    a backend leaking 101 foreign rows the view drops terminates after one query
    instead of looping (`test_scoped.py`, fake guarded by `QUERY_BUDGET`).

## Verification

```sh
.venv/bin/python -m pytest -q tests/arch/database
make lint-arch
make type
make sec
make check   # integrator
```

## Rollout

No flag. Strictly additive at the API level (new protocol member plus the
`walk_pages` free function; existing signatures unchanged). Behavioural change is
the bug fix itself: tenant listings now return pages beyond the first 100 rows —
module page-walkers (`agents.list_agents`, `auth.repository._all`,
`wallet.list_templates`, `platform/repository_store._rows`) see the full set;
page-1-only readers (`tools.list_all`, `voices.list_voices`, `voice.list_partners`,
`wallet.list_ledger`) and the `find_many`-as-listing read in `catalog.list_all`
still cap at `MAX_PAGE_SIZE` until they walk pages (`walk_pages` is the one-line
fix; owners listed under *Burn-down*). Rollback is a revert of the four database
files. No migration; the index in *Data model* is an optional performance
follow-up.

## Burn-down

- [x] Red test proving the 100-row truncation through the scoped view
- [x] `BaseRepository.list_where` + docstrings (`list`, `find_many` re-scoped as a bounded lookup)
- [x] `InMemoryRepository.list_where` (filter active rows, window)
- [x] `MotorRepository.list_where` (compound active selector, sort, skip/limit, count) and driver-field constants
- [x] `TenantScopedRepository.list` over `list_where`, fail-closed `list_where`
- [x] Fakes updated (`test_scoped._FakeRepository`, motor `_Collection` honours arbitrary selectors)
- [x] Tests: paging past 100, isolation across pages, memory + Motor unit coverage
- [x] `voiceai/database/README.md` and `CONTRACT.md`
- [x] Gates: database tests, `make lint-arch`, `make type`, `make sec`
- [x] `find_many` clamp pinned in both backends (`test_find_many_is_a_bounded_lookup`)
- [x] `walk_pages` shared page-walker + tests (order, filter, one query per page, leak-guard termination); documented in README/CONTRACT
- [x] Follow-ups, round 2 (`page-walkers` batch). These supersede the two call-site
  *Non-goals* above and the "still cap at `MAX_PAGE_SIZE`" sentence under *Rollout*:
  every listed reader walks `walk_pages`, and each site has a test inserting more than
  `MAX_PAGE_SIZE` rows for one tenant plus rows for another, asserting every own row
  and no foreign row comes back.
  - [x] `tools.repository.list_all` walks its view (system and tenant views alike);
    test in `modules/tools/tests/test_service.py`
  - [x] `voices.repository.list_voices` walks the tenant view; the `agent_id` filter
    stays in process (unchanged contract); test in `modules/voices/tests/test_repository.py`
  - [x] `voice.repository.list_partners` walks the partner view and drops its inline
    `page_size=100` literal; the voice module is at its line budget, so the test lives
    at `tests/arch/platform/test_partner_listing.py`
  - [x] `catalog.repository.list_all` walks `{TENANT_ID_FIELD: SYSTEM_TENANT_ID}`
    instead of using the bounded `find_many` as a listing; test in
    `modules/catalog/tests/test_repository.py`
  - [x] `wallet.repository.list_ledger`: the `entry_type` filter is pushed down to the
    driver (`walk_pages(ledger, {type: entry_type})`), every page is walked, and the
    result is the newest `limit` matches, newest first (the legacy `MemoryStore`
    contract). Before, page 1 of the oldest-first listing was reversed, so a ledger
    longer than `limit` returned its *oldest* rows and a type filter only saw that
    window. A non-positive `limit` returns nothing. `list_templates` collapses its
    hand-written `has_next` loop (and inline `page_size=100`) onto the walker. The
    default limit comes from `wallet.constants.DEFAULT_LEDGER_LIMIT` in the port, the
    repository and the service. Tests in `modules/wallet/tests/test_repository.py`
  - [x] `platform/repository_store._rows` walks the tenant-scoped repository
    (`self._scoped(collection)`), so a tenant listing costs that tenant's pages, not
    the whole collection; `_pages`/`scan_family` stay as the deliberate tenant-blind
    inbound read (now over the walker too). Module docstring debt bullet updated;
    test in `tests/arch/platform/test_repository_store.py`
  - [x] Gates: the six sites' tests, `tests/arch`, `make lint-arch`, `make type`, `make sec`
- [ ] Follow-up (other owners): the two remaining hand-written `has_next` loops
  (`agents.list_agents`, `auth.repository._all`) can collapse onto `walk_pages`;
  `voices.list_voices` can push its `agent_id` filter down once the module owns a
  field-name constant; compound `(tenant_id, created_at, _id)` index in the migration tool
