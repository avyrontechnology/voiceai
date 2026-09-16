# Spec 0001 — Architecture foundation (common, core, database, first module)

- **Status:** done
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** nothing (this is the root spec)

## Goal

Stand up the target submodule architecture defined in `AGENTS.md`: the `common`, `core`, and
`database` packages, one reference feature module (`modules/health`) that demonstrates every
layer, and the test tree that mirrors them. Everything later migrates into this shape.

## Non-goals

- Touching the realtime engine (`voiceai/agent_manager`, transcribers, synthesizers, handlers, s2s).
- Migrating any existing platform feature (that is spec 0002+).
- Adding new runtime dependencies. Only `fastapi`, `pydantic`, `redis`, `httpx`,
  `python-dotenv` from the existing `requirements.txt` may be imported.
- Choosing/wiring a real database driver (interface + in-memory implementation only).

## Package layout (new code only)

```
voiceai/
  common/                 # project-wide, dependency-free building blocks
    __init__.py           # explicit __all__ re-exports
    constants.py          # APP_NAME, LOGGER_NAME, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ...
    errors.py             # ErrorCode + AppError hierarchy
    exceptions.py         # guard helpers: ensure(), ensure_found(), ensure_valid()
    logger.py             # the single otobaai logger
    responses.py          # API response envelopes + FastAPI exception handlers
    pagination.py         # PaginationParams, Page[T], paginate()
    datetime_utils.py     # utc_now, isoformat_z, parse_iso, to_ist, epoch_ms
    security.py           # redact_secrets(), is_safe_outbound_url()
  core/
    __init__.py
    environment.py        # Environment model; get_environment() cached loader
    container.py          # DI container + FastAPI request accessor
    redis.py              # redis client factory (lazy, from Environment)
    db.py                 # database client factory (backend-selected, lazy)
    app_factory.py        # create_app(): middleware, handlers, module routers
  database/
    __init__.py
    constants.py          # Collections enum — every collection/table name lives here
    base.py               # BaseFields model: audit fields all documents inherit
    repository.py         # BaseRepository protocol + InMemoryRepository[TModel]
  modules/
    __init__.py           # ModuleDef + ALL_MODULES registry (composition data, no logic)
    health/
      __init__.py         # exports MODULE (a ModuleDef)
      constants.py  models.py  errors.py  exceptions.py
      repository.py service.py controller.py
      utils.py      helpers.py static_methods.py
tests/arch/
  __init__.py  conftest.py            # FakeRedis + app/container fixtures (local, offline)
  common/    test_errors.py test_responses.py test_pagination.py test_datetime_utils.py
             test_logger.py test_security.py test_exceptions.py
  core/      test_environment.py test_container.py test_app_factory.py test_db.py
  database/  test_base.py test_constants.py test_memory_repository.py
  modules/health/ test_static_methods.py test_service.py test_repository.py test_controller.py
```

## Interface contracts (binding for implementers)

Both build agents code against these exact names/signatures. Deviations require editing this
spec first.

**Import direction (binding, prevents cycles):** `modules → core` is the only static
direction. `core/container.py` and `core/app_factory.py` import `voiceai.modules` **lazily
inside function bodies**; `modules/__init__.py` uses `from __future__ import annotations` +
`TYPE_CHECKING` for the `Container` annotation. Controllers may import
`core.container.get_container` (composition access only — AGENTS.md §3). `common/responses.py`
is the single `common` file allowed to import fastapi. Every `__init__.py` carries a one-line
docstring (ruff D104). `LOGGER_NAME` is defined once, in `common/constants.py`;
`common/logger.py` imports it.

### common/errors.py

```python
class ErrorCode(str, Enum):
    INTERNAL_ERROR / INVALID_REQUEST / NOT_FOUND / CONFLICT / UNAUTHORIZED / FORBIDDEN
    RATE_LIMITED / DEPENDENCY_UNAVAILABLE / CONFIGURATION_ERROR / DATABASE_ERROR
    # values are lower_snake strings, e.g. "not_found"

class AppError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    retryable: bool = False
    def __init__(self, message: str, *, details: dict[str, Any] | None = None,
                 cause: BaseException | None = None) -> None    # sets .error_id (12-hex uuid slice)
    @property public_message -> str      # INTERNAL_ERROR hides message: "Something went wrong (ref <error_id>)"
    def to_dict(self) -> dict[str, Any]  # code, message=public_message, error_id, retryable, details?

class InvalidRequestError(AppError)  400   class NotFoundError(AppError)        404
class ConflictError(AppError)        409   class UnauthorizedError(AppError)    401
class ForbiddenError(AppError)       403   class RateLimitedError(AppError)     429  retryable=True
class DependencyUnavailableError(AppError) 503 retryable=True
class ConfigurationError(AppError)   500   # extra kwarg: path: str | None -> details["path"]
class DatabaseError(AppError)        500
```

### common/exceptions.py

```python
def ensure(condition: bool, message: str, *, error: type[AppError] = InvalidRequestError) -> None
def ensure_found(value: T | None, message: str) -> T          # raises NotFoundError
def ensure_valid(condition: bool, message: str) -> None        # raises InvalidRequestError
```

### common/logger.py  (rule 3: the single "otobaai" logger)

```python
LOGGER_NAME = "otobaai"
def get_logger(module: str | None = None) -> logging.Logger   # "otobaai" or "otobaai.<module>"
def configure_logging(level: str | None = None) -> None       # idempotent; one StreamHandler on
                                                              # the root "otobaai" logger; format:
                                                              # ts level [request_id] name: message
def set_request_id(request_id: str | None) -> None            # contextvar behind a logging.Filter
def get_request_id() -> str | None
```

### common/responses.py  (rule 2: all API responses come from here)

```python
class ApiMeta(BaseModel): request_id: str | None = None; pagination: dict | None = None
def success_response(data: Any = None, *, message: str | None = None,   # data: Any -> # why: envelope carries arbitrary payloads
                     meta: ApiMeta | dict | None = None, status_code: int = 200) -> JSONResponse
    # body: {"ok": true, "data": ..., "message": ..., "meta": {...}}
    # data/meta pass through fastapi.encoders.jsonable_encoder (datetimes + BaseModels safe)
def error_payload(err: AppError) -> dict           # {"ok": false, "detail": public, "error": err.to_dict()}
def error_response(err: AppError) -> JSONResponse  # status = err.http_status
def paginated_response(page: "Page[Any]", *, message: str | None = None) -> JSONResponse
def register_exception_handlers(app: FastAPI) -> None
    # AppError -> envelope; starlette.exceptions.HTTPException (NOT the fastapi subclass — that
    #   key would miss router-raised 404/405) -> envelope, detail + headers preserved;
    # RequestValidationError -> 422 envelope, details["errors"]; Exception -> 500 envelope that
    # NEVER includes str(exc); logs with error_id + stack via the otobaai logger.
```

### common/pagination.py

```python
class PaginationParams(BaseModel): page: int = 1 (ge=1); page_size: int = DEFAULT_PAGE_SIZE (ge=1, le=MAX_PAGE_SIZE)
    @property skip -> int; @property limit -> int
class Page(BaseModel, Generic[T]): items: list[T]; total: int; page: int; page_size: int
    @property pages -> int; @property has_next -> bool
def paginate(items: Sequence[T], total: int, params: PaginationParams) -> Page[T]
```

### common/datetime_utils.py

`utc_now() -> datetime` (aware, UTC) · `isoformat_z(dt) -> str` · `parse_iso(text) -> datetime`
· `to_ist(dt) -> datetime` (UTC+05:30 via `timezone(timedelta(hours=5, minutes=30))`, no new dep)
· `epoch_ms(dt | None) -> int`.

### common/security.py

```python
SECRET_KEY_PATTERN  # re: api_key|apikey|token|secret|password|authorization|credential  # noqa: S105
def redact_secrets(mapping: Mapping[str, Any]) -> dict[str, Any]   # deep copy, values -> "***"
def is_safe_outbound_url(url: str, *, resolver: Callable[..., Any] = socket.getaddrinfo) -> bool
    # https/http only. Hostnames are RESOLVED via the injectable resolver and every returned
    # address must be public: loopback, private, link-local, CGNAT, and metadata
    # (169.254.169.254) ranges are blocked — a hostname pointing at 10.x fails. Resolution
    # failure -> False. Docstring notes the DNS TOCTOU limitation (pin the IP at connect time
    # for high-risk calls). Tests inject a fake resolver; no network.
REQUEST_ID_PATTERN  # re: ^[A-Za-z0-9_-]{1,64}$ — inbound X-Request-ID must match or is replaced
```

### core/environment.py  (rule 4)

```python
class Environment(BaseModel):
    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    redis_url: str = ""                       # empty -> redis features off
    db_backend: Literal["memory", "mongo"] = "memory"
    db_url: str = ""; db_name: str = "otoba"
    allowed_origins: list[str] = []           # env ALLOWED_ORIGINS, comma-separated
    cookie_secure: bool | None = None         # env COOKIE_SECURE; None -> decided by app_env
    @property is_prod -> bool
    @property cookie_secure_effective -> bool # cookie_secure if set, else is_prod
def load_environment(dotenv_path: str | Path | None = None) -> Environment
    # load_dotenv(override=False) once; env var names are the UPPER_SNAKE of the fields;
    # invalid values raise ConfigurationError(path=<VAR>)
def get_environment() -> Environment          # cached; reset_environment() for tests
```

### core/container.py  (rule 9)

```python
class Container:
    def register(self, key: type[T] | str, provider: Callable[["Container"], T] | T, *, singleton: bool = True) -> None
        # re-registering an existing key REPLACES the provider and drops any cached singleton
        # (test fixtures rely on this to swap in fakes after build_container)
    @overload resolve(key: type[T]) -> T
    @overload resolve(key: str) -> Any        # why: string keys are untyped by nature; cast() inside
    def resolve(self, key): ...               # unknown key -> ConfigurationError
    def has(self, key: type[T] | str) -> bool
    async def aclose(self) -> None            # closes the "redis" client when present; idempotent
def build_container(env: Environment | None = None, *, modules: "Sequence[ModuleDef] | None" = None) -> Container
    # registers: Environment, logging (configure_logging), redis client (key "redis", may be None),
    # db client (key "db"); then calls register(container) for each ModuleDef — modules=None means
    # a LAZY in-function import of voiceai.modules.ALL_MODULES; tests pass modules=[] to isolate
def get_container(request: Request) -> Container         # FastAPI dependency reading app.state.container
```

### core/redis.py / core/db.py

```python
def create_redis(env: Environment) -> "redis.asyncio.Redis | None"
    # ASYNC client (sync ping would block the loop — AGENTS.md §5). None when redis_url empty;
    # from_url(..., decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
async def ping_redis(client: "redis.asyncio.Redis | None") -> bool   # False on None or any exception
def create_db(env: Environment) -> "DatabaseClient"           # "memory" -> InMemoryDatabase();
                                                              # "mongo" -> ConfigurationError("driver not installed; see spec 0003")
class DatabaseClient(Protocol): name: str
class InMemoryDatabase: name = "memory"; collections: dict[str, dict[str, dict]]
```

### core/app_factory.py

```python
def create_app(env: Environment | None = None, *, container: Container | None = None,
               modules: Sequence[ModuleDef] | None = None) -> FastAPI
    # configure_logging; container = container or build_container(env, modules=modules);
    # app.state.container = container; lifespan handler awaits container.aclose() on shutdown;
    # RequestIdMiddleware: inbound X-Request-ID kept only if it matches REQUEST_ID_PATTERN
    #   (else a fresh uuid4 hex) -> set_request_id + echoed on the response;
    # CORSMiddleware(allow_credentials=True, explicit allowlist) added only when
    #   env.allowed_origins is non-empty;
    # register_exception_handlers(app); include each module router at prefix "/api/v1";
    # modules=None -> LAZY import of voiceai.modules.ALL_MODULES inside the function body
```

### database/*

```python
# constants.py
class Collections(str, Enum): USERS="users"; AGENTS="agents"; EXECUTIONS="executions"; HEALTH_CHECKS="health_checks"
# base.py  (rule 5)
class BaseFields(BaseModel):
    id: str | None = None
    created_at: datetime = Field(default_factory=utc_now); updated_at: datetime = Field(default_factory=utc_now)
    created_by: str | None = None; updated_by: str | None = None
    is_active: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
    def touch(self, user_id: str | None = None) -> None   # bump updated_at/updated_by
# repository.py
class BaseRepository(Protocol[TModel]):
    async def insert(self, model: TModel) -> TModel                     # assigns id (uuid4 hex) if missing
    async def get(self, item_id: str) -> TModel | None                  # only is_active
    async def list(self, params: PaginationParams) -> Page[TModel]      # is_active only, insertion order
    async def update(self, model: TModel) -> TModel                     # NotFoundError if missing; touch()
    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool
class InMemoryRepository(Generic[TModel]):   # implements the protocol over InMemoryDatabase
    def __init__(self, db: InMemoryDatabase, collection: Collections, model_type: type[TModel]) -> None
```

### modules/__init__.py + modules/health

```python
@dataclass(frozen=True)
class ModuleDef: name: str; router: APIRouter; register: Callable[[Container], None]
ALL_MODULES: tuple[ModuleDef, ...]   # (health.MODULE,)

# health module — full canonical file set, all layers exercised FOR REAL:
# constants.py: MODULE_NAME="health"; ROUTE_PREFIX="/health"; COMPONENT_REDIS/COMPONENT_DATABASE/COMPONENT_APP
# models.py: HealthState(str, Enum) UP/DOWN/SKIPPED;
#            ComponentHealth(BaseModel) name: str, state: HealthState, detail: str | None = None,
#                                       latency_ms: float | None = None;
#            HealthReport(BaseModel) status: HealthState, components: list[ComponentHealth], version, uptime_s;
#            HealthCheckRecord(BaseFields) note: str = "heartbeat"   # persisted probe record
# errors.py: HealthCheckError(DependencyUnavailableError)
# repository.py: HealthRepository(redis_client, db_client)  # infra probes ONLY (rule 1d)
#            async def probe_redis() -> ComponentHealth      # SKIPPED when client is None
#            async def probe_database() -> ComponentHealth   # REAL round trip: insert->get a
#              HealthCheckRecord through InMemoryRepository(db, Collections.HEALTH_CHECKS,
#              HealthCheckRecord) when isinstance(db, InMemoryDatabase); other backends report
#              ComponentHealth(detail=f"backend={db.name}") until spec 0003
# service.py: HealthService(repo: HealthRepository, logger: Logger, started_at: datetime)
#            async def report() -> HealthReport; async def liveness() -> HealthState   (business logic, rule 1e)
#            async def readiness() -> HealthReport  # raises HealthCheckError when overall DOWN
# controller.py: APIRouter(prefix=ROUTE_PREFIX); paths "" (report), "/live", "/ready" — empty-string
#            path, not "/", to avoid a 307 redirect on GET /api/v1/health. /ready returns the
#            report envelope or lets HealthCheckError surface as the 503 envelope.
#            Dependency style: Annotated[HealthService, Depends(get_health_service)] (B008-safe);
#            get_health_service resolves via core.container.get_container; no logic (rule 1f)
# static_methods.py: overall_status(components) -> HealthState   # pure: DOWN if any DOWN else UP; SKIPPED ignored
# utils.py: uptime_seconds(started_at, now=None) -> float
# helpers.py: component_summary(components) -> str  # "redis=up database=up" for log lines
# register(container): builds HealthRepository + HealthService from container entries
```

## Security notes

`register_exception_handlers` never returns `str(exc)` for unexpected exceptions; secrets are
redacted with `redact_secrets` before any config/log dump; `is_safe_outbound_url` exists for
every future outbound call; env is the only config source (rule: no literals in code).

## Test plan (rule 10)

Offline only. `FakeRedis` stub in `tests/arch/conftest.py` (**async** `ping`, ok / raising
variants). Binding pins learned in architecture review:
- The 500-leak test uses `ASGITransport(app, raise_app_exceptions=False)` — Starlette's error
  middleware re-raises after responding.
- Importing anything under `voiceai.*` executes the legacy package `__init__` (basicConfig +
  a LogRecord-factory swap), so the `otobaai` logger sets `propagate=False` and logger tests
  attach a handler directly to it — root-based `caplog` sees nothing.
- An autouse fixture calls `reset_environment()`; app fixtures pass an explicit
  `Environment(...)` into `create_app` so a developer's stray `.env` cannot flip results.
- Fixtures inject fakes by re-registering keys (e.g. `"redis"`) after `build_container` —
  which is why `register()` must replace and drop cached singletons.
- `tests/arch/conftest.py` builds the full app (default modules) **lazily inside fixtures**,
  never at import time, so half-written peer files during parallel builds cannot break
  collection.
- At least one test makes protocol conformance explicit for mypy:
  `repo: BaseRepository[HealthCheckRecord] = InMemoryRepository(...)`.
Every contract above has behavior tests: error envelope shapes (incl. 500 never leaking a
planted secret string), pagination math + bounds, logger idempotence + request-id in records,
env parsing + `ConfigurationError` paths, container singleton vs factory + unknown key,
in-memory repository CRUD + soft-delete + pagination, health service aggregation (redis down →
DOWN, redis unconfigured → SKIPPED + overall UP), controller end-to-end via
`httpx.AsyncClient(transport=ASGITransport(app=create_app(...)))` asserting the envelope and
`X-Request-ID` echo. Target ≥ 85% coverage on the new packages.

## Verification

`make check` green: ruff (repo rules) + ruff strict on new packages + mypy (new packages) +
`pytest tests/arch`. `make sec` (bandit on new packages) clean at medium/high. `make cov`
enforces the ≥ 85% line — `pytest-cov` joins the dev extras for this (dev-only dependency,
maintained by pytest-dev; supply-chain note per AGENTS.md §4).

## Implementation notes (deviations absorbed after the adversarial review)

Accepted as the contract of record (all verified by the 59-agent review workflow):
- `Container.register` carries `@overload`s (typed key → `T`, string key → `Any`); error-class
  attributes (`code`, `http_status`, `retryable`) are `ClassVar`; `Environment` is frozen.
- `ALLOWED_ORIGINS` **rejects `"*"`** (and non-origin entries) with
  `ConfigurationError(path="ALLOWED_ORIGINS")` on EVERY model construction path (pydantic
  field validator); the field is an immutable `tuple[str, ...]`; and `_add_cors` re-checks at
  the point of use, covering `model_copy(update=...)`, which skips validators.
- Unexpected-exception (500) envelopes are produced inside `RequestIdMiddleware` (with the
  Starlette server-error handler as backstop) so they carry `X-Request-ID` and pass CORS.
- `InMemoryRepository.update` treats a soft-deleted document as absent (`NotFoundError`);
  `insert` with an explicit id **replaces** that document (the heartbeat probe's contract) and
  `insert`/`update` return deep copies.
- HTTPException mapping preserves `detail` + headers for 4xx; **5xx detail is folded into an
  opaque envelope** (internals must not leak — supersedes the unqualified "detail preserved").
- `APP_VERSION` resolves via `importlib.metadata.version("voiceai")` (fallback `0.0.0+local`)
  so `/health` reports the deployed package version; the FastAPI app uses the same value.
- Health: `register()` binds lazy providers (no `Environment` resolution — no seam needs it);
  three components (`app`, `redis`, `database`); non-memory backends report `SKIPPED` with
  `detail="backend=<name>"` (`# TODO(spec-0003)`); heartbeat reuses one well-known id so a
  polled `/health` cannot grow the store.
- Redis factory tests live in `tests/arch/core/test_container.py` (the container owns the
  client's lifetime); an explicit `dotenv_path` is loaded per call — only the default `.env`
  lookup is once-per-process.
- `is_safe_outbound_url` resolves via `socket.getaddrinfo` (blocking): async call sites must
  wrap it in `asyncio.to_thread` — an async helper ships with the first async consumer spec.
- Version duality with the legacy `voiceai.__version__` string resolves at merge-to-master.

## Rollout

Pure addition; nothing imports the new packages yet. `local_setup` wiring and feature
migrations are follow-up specs (0002: agents module; 0003: DB driver choice; 0004: voice
module; 0005: platform auth migration).
