# Spec 0003 — Database driver selection + real backend for `core/db.py`

- **Status:** done (landed on revamp/arch; mongo opt-in, memory default unchanged)
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** spec 0001 (foundation: `common`, `core`, `database`, `BaseRepository`); AGENTS.md §3–§6

## Goal

`DB_BACKEND=mongo` stops raising `ConfigurationError` and connects to a real MongoDB:
an async-native driver behind the existing `DatabaseClient` protocol, a
`MotorRepository` implementing `BaseRepository` with byte-identical semantics to
`InMemoryRepository`, and a real write-proof health probe for the mongo backend. The
default (`memory`) behaves exactly as today, and every existing suite keeps passing —
the one test that pins mongo-as-unimplemented is rewritten same-commit with a
reconciliation table.

## Non-goals

- An ODM (beanie or other): the codebase owns its models (`BaseFields`) and its
  repository pattern; an ODM would fight both for no contract gain.
- A migrations framework, multi-database sharding, or replica-set topology management.
- Changing the `BaseRepository` shape, the `Collections` registry, or any module
  repository: they code against the protocol, which does not change.
- Touching `platform/` (spec 0005+) or the realtime engine.
- Seeding data, backfills, or production cutover: rollout stays opt-in per deployment.

## Design

**Driver: motor** (async-native MongoDB driver, same maintainers as pymongo). pymongo is
sync — every repository call would need `asyncio.to_thread` to respect AGENTS.md §5
(no blocking I/O on the event loop), wrapping the entire repository layer for zero
benefit. motor speaks asyncio natively, its API mirrors pymongo (team knowledge
transfers), and it is actively maintained. New dependency, declared in
`requirements.txt` with this rationale (AGENTS.md §4 supply chain).

**`core/db.py` gains `MotorDatabase`** (rule 4: process wiring lives in core):

- Holds an `AsyncIOMotorClient` + the selected database; `name = "mongo"`.
- Constructor takes the resolved `db_url`/`db_name` (never reads env itself), plus an
  optional `client_factory` (defaults to motor's client): the seam that makes timeout
  wiring unit-testable and keeps the `motor` import out of module scope. The factory
  default imports motor lazily *inside* itself, so an environment without the driver
  installed still imports this module.
- `close()` shuts the client down; the container's `aclose` calls it (same pattern
  as redis — close must never *create* a connection).
- Error precedence in the mongo branch: empty `db_url` fails first
  (`ConfigurationError`, no driver needed to report it); a missing driver fails next,
  naming the package. Failing at startup beats discovering either on the first
  request (the rule `create_db` already documents); the current "spec 0003" error
  text retires with this spec.

**`database/repository.py` gains `MotorRepository[TModel]`** implementing
`BaseRepository` over one motor collection, mirroring `InMemoryRepository` exactly:

- Same constructor shape `(db, collection, model_type)`, except `db` is the motor
  database handle (typed as `Any` with a `# why:` — the driver type must not leak
  into the signature; rule: repositories take and return module models, never raw
  driver documents/rows — rule 1d).
- `_id` mapping, pinned both directions: the model's `id` is stored as Mongo's
  `_id` **as a string** (never `ObjectId` — no conversion layer to get wrong);
  the stored document is `model_dump(exclude={"id"}) + {"_id": id}`; reads map
  `_id` back to `id` before `model_validate`. `updated_at`/`created_at` ride the
  document as BSON datetimes (both timezone-aware UTC by `BaseFields` contract).
- insert/get/list/update/soft_delete with identical *observable* semantics, via
  atomic single ops (no verbatim-read-then-write constraint exists here — this is
  new code, only the contract is pinned): `replace_one({"_id": id}, doc,
  upsert=True)` for insert (preserves insert-replaces, the only resurrection path);
  `find_one({"_id": id})` + active check for get; `replace_one({"_id": id,
  "is_active": True}, touched_doc)` + `matched_count == 0` → `NotFoundError` for
  update (the single op cannot distinguish missing from soft-deleted, and both
  raise identically — the race the two-step legacy shape had simply cannot occur);
  `update_one({"_id": id, "is_active": True}, {"$set": ...})` + matched check for
  soft_delete, where the `$set` omits `updated_by` when `user_id is None`
  (mirroring `touch()` exactly); `find({"is_active": True})` server-side with
  `sort(created_at asc, _id asc)` + `skip`/`limit` and a same-filter
  `count_documents` for listing, paginated through `common.paginate`. The
  created_at+_id sort is a deliberate, documented divergence from the in-memory
  insertion order (uuid-hex ids carry no time order; same-ms inserts need the
  tiebreak for determinism).
- Timeouts live at CLIENT construction (`serverSelectionTimeoutMS`,
  `timeoutMS`, `connectTimeoutMS`, `socketTimeoutMS` from `database/constants.py`,
  rule 1b), covering every operation — no per-call kwarg the driver may or may
  not accept. The client_factory seam asserts the exact kwargs in tests.
- No string-built queries anywhere: filter dicts only, parameterized by arguments.

**Health probe goes backend-dispatched, not `isinstance`-gated-out** (rule: probe what
you run): `modules/health/repository.py` keeps the in-memory round trip untouched
(same module-global `InMemoryRepository` seam the existing tests monkeypatch) and
adds the identical round trip through a module-global `MotorRepository` for
`MotorDatabase`. Backends that are neither keep the existing `SKIPPED` answer, so
`test_database_probe_names_a_backend_it_cannot_verify` (whose `RemoteDatabase` is
neither) passes UNMODIFIED. No `ping()` method is added to `DatabaseClient` — the
round trip *is* the reachability proof, and an uncalled method would be dead code
(rule 8).

**Container**: `build_container` already routes `CONTAINER_KEY_DB` through
`create_db` (unchanged); `aclose` additionally closes a built motor client behind
the same never-create discipline as redis — structured as two independent closes,
because `aclose` today returns early when redis is `None` (the default dev setup),
which would silently skip the db close if merely appended after.

## Interface contracts

```python
# core/db.py
class MotorDatabase:
    name: str  # "mongo"
    def __init__(
        self,
        db_url: str,
        db_name: str,
        *,
        client_factory: Callable[..., Any] | None = None,  # why: test seam + lazy motor import
    ) -> None: ...
    def close(self) -> None: ...

def create_db(env: Environment) -> DatabaseClient: ...  # unchanged signature

# database/repository.py
class MotorRepository(Generic[TModel]):
    def __init__(self, db: Any, collection: Collections, model_type: type[TModel]) -> None: ...
    async def insert(self, model: TModel) -> TModel: ...
    async def get(self, item_id: str) -> TModel | None: ...
    async def list(self, params: PaginationParams) -> Page[TModel]: ...
    async def update(self, model: TModel) -> TModel: ...  # raises NotFoundError as specified
    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool: ...

# database/constants.py
MONGO_TIMEOUT_MS: Final[int] = 5000
MONGO_SERVER_SELECTION_TIMEOUT_MS: Final[int] = 5000
MONGO_CONNECT_TIMEOUT_MS: Final[int] = 5000
MONGO_SOCKET_TIMEOUT_MS: Final[int] = 20000

# modules/health/repository.py (additive only)
from database.repository import MotorRepository  # module-global seam, same as InMemoryRepository
```

`MotorRepository` takes the motor *database* handle (not client, not collection):
`db[collection.value]` selects the collection, so one handle serves every repository.
Fakes implement a fake handle whose `__getitem__` returns a fake collection with
`replace_one` / `find_one` / `find` (cursor supporting `sort`/`skip`/`limit`/
`to_list`) / `update_one` / `count_documents`, each recording kwargs so the suite
asserts filter shapes — never real query semantics beyond what the tests script.

## Data model

No model changes. Every persisted model already inherits `database.base.BaseFields`;
collection names already come from `Collections`. Documents store `model_dump()` output
with the model's `id` mapped to Mongo's `_id` at the repository boundary (both
directions), so re-insertion under a known id keeps the upsert-by-id contract.

## Security notes

- **Secrets:** `db_url` often embeds credentials; it already rides `_SENSITIVE_ENV_FIELDS`
  (redacted before logging) and stays a plain `str` (upgrading to `SecretStr` would ripple
  through every test constructing `Environment`; recorded as rejected with rationale).
  Never logged, never committed (`.env` git-ignored).
- **Injection:** filter dicts only, built from typed arguments; no string-built queries,
  no `eval`, no `$where`.
- **Timeouts:** `serverSelectionTimeoutMS` at client construction + `timeoutMS` per
  operation (constants above); a hung database degrades probes instead of wedging loops
  (health already isolates per-call failures; `CancelledError` still propagates).
- **Error opacity:** driver exceptions never reach clients as text — repositories raise
  the existing `NotFoundError`/domain errors; the health probe reports DOWN with a
  generic detail (existing pins extended, not weakened).
- **Supply chain:** one new dependency (`motor`); `pip-audit` runs when touching
  requirements (this spec does).
- **No new external surface** (no routes, no controller changes).

## Test plan

- `tests/arch/core/test_db.py`: memory behavior unchanged; mongo-without-driver →
  `ConfigurationError` naming the package; mongo-with-empty-url → `ConfigurationError`
  at startup; `MotorDatabase` satisfies `DatabaseClient` structurally (the spec-0001
  annotation idiom). The `test_mongo_fails_at_startup_with_the_spec_reference` test is
  rewritten same-commit (mongo now constructs; the unimplemented-backend pin has no
  subject left) with a reconciliation table.
- `tests/arch/database/test_motor_repository.py` (new): full `BaseRepository` contract
  against an in-memory fake async collection (DI fakes per rule 10 — no server, offline
  only): insert-assigns-id, insert-replaces (resurrection), get-skips-soft-deleted,
  update-misses-and-updates-deleted-raise-`NotFoundError`, soft-delete idempotency
  (incl. `updated_by` omitted when `user_id is None`), paginated active-only listing
  with created_at+_id ordering, `_id`-as-string filters asserted on the fake.
- `tests/arch/core/test_db.py`: client-factory seam asserts the exact client-construction
  timeout kwargs; missing-driver simulation via `sys.modules` poisoning (offline-safe).
- `tests/arch/modules/health/test_repository.py`: existing pins unchanged (memory
  round trip, SKIPPED unknown, DOWN paths); new: mongo round trip UP through a faked
  `MotorRepository` seam, mongo write-failure DOWN without leaking driver text.
- `tests/arch/core/test_container.py`: `aclose` closes a built motor client and stays
  idempotent without creating one.
- Offline only: no live MongoDB anywhere in the suite (no network, no credentials).

## Verification

- `make check` (ruff + strict arch lint + mypy + `pytest tests/arch`) green.
- `make sec` (bandit) green; `pip-audit` reviewed for the motor addition.
- `make test-all`: zero net-new failures vs the re-snapshotted baseline + reconciliation.
- `make cov` under the B13a-refined gate: new code (db motor path, repository, probe
  branch) covered by the suites above.

## Rollout

- Opt-in per deployment: default stays `memory`; setting `DB_BACKEND=mongo` + `DB_URL`
  (+ optional `docker compose` mongo service for local dev — compose file change ships
  in this spec, service off by default) switches the backend with no code changes.
- Revert plan: flip `DB_BACKEND` back to `memory` (no data migration exists yet, and
  none is introduced — memory and mongo stores are independent).
- Follow-ups (not this spec): production cutover runbook, index creation per
  collection, backup/restore story, `spec-0005` auth repositories building on
  `MotorRepository`.

## Closeout

Landed as specified with nine architect-review findings folded in before code
(`_id`-as-string mapping both directions, atomic single-op writes, `touch()`-exact
`$set`, server-side sort/paginate with created_at+_id order, no `ping()` on the
protocol, aclose restructured past the redis early-return, lazy motor import behind
a recording-fake client_factory seam, corrected fake surface, followup-task
placement). Reconciliation: `test_mongo_fails_at_startup_with_the_spec_reference`
rewritten 1↔1 (mongo constructs now; missing-driver and empty-url pins added) —
no other legacy test touched. Gates: `make check` pieces green (ruff ×2; mypy
blocked by the pre-existing numpy-stub env failure, identical on baseline),
`make test-all` 8 failed (7 known master + 1 env, zero net-new) / 2,685 passed /
2,693 collected (+18: 9 motor-repository, 5 db-factory, 2 health, 2 container),
`make sec` clean, `make cov` 86.08% green, `pip-audit` clean for motor/pymongo
(pre-existing aiohttp/starlette findings left untouched). Rollout stays opt-in
(`DB_BACKEND=mongo` + `DB_URL` + compose `mongo` profile).
