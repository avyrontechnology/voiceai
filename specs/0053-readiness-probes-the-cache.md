# Spec 0053: readiness probes the Redis the app uses

> Bug class: a readiness report that answers for a dependency the app does not use,
> and stays silent about the one it does. Found while reviewing spec 0048's deploy
> configuration: the docs already describe the fixed behaviour, the code does not
> have it.

## Goal

After spec 0048 the only Redis the single app talks to is the **cache client**,
`container.redis_cache` (`core.redis.create_redis_cache`: `REDIS_CACHE_URL`, legacy
`REDIS_URL` fallback via `Environment.redis_cache_url_effective`). It backs the JWT
revocation denylist (`MongoAuthStore(cache=...)`).

The health module's Redis component is still built from `container.redis_client`
(`create_redis`, keyed only on `REDIS_URL`). Two production-visible defects follow:

1. **Documented production config reads "skipped".** `.env.sample`, `README.md`,
   `local_setup/README.md` and `voiceai/platform/RUNBOOK.md` prescribe
   `REDIS_CACHE_URL` set and `REDIS_URL` empty. With that config
   `GET /api/v1/health` and `GET /api/v1/health/ready` report
   `redis: skipped ("redis not configured")` while every JWT-authenticated request
   depends on that Redis. An outage of the cache is invisible to the load balancer.
2. **Split URLs ping the wrong server.** With `REDIS_URL` pointing somewhere else
   (the legacy single URL, a broker) and `REDIS_CACHE_URL` at the cache, readiness
   pings the `REDIS_URL` server: it can take a healthy instance out of rotation over
   a server the app never calls, or report `up` while the cache is down.

Reproduction (before this spec, offline):

```python
env = Environment(redis_url="", redis_cache_url="redis://cache:6379", db_backend="memory")
container = build_container(env)
container.redis_client()                                  # None
container.redis_cache()                                   # a client
await container.health_repository().probe_redis()         # SKIPPED "redis not configured"
```

After this spec the readiness probe pings the **effective cache client** — the same
object the auth store holds — and reports `SKIPPED` only when no cache URL is
effective (`REDIS_CACHE_URL` and `REDIS_URL` both empty).

## Non-goals

- Liveness. `GET /api/v1/health/live` touches no dependency, before and after.
- Removing `container.redis_client`. Tests and the shutdown path resolve and
  override it; it stays as the **legacy single-URL alias** (see *Interface
  contracts*). Retiring the provider is a follow-up.
- Wiring the shared login throttle. See *Findings* — it is a real gap, but it is
  outside this batch's file set and it changes the Redis cost accounting that
  `tests/arch/test_redis_isolation.py` pins, so it needs its own spec.
- Probing `REDIS_BROKER_URL` / `REDIS_RESULT_URL`. Nothing reads them today.
- Renaming the reported component (`redis`) or its details: the payload shape is
  unchanged.
- Changing `tests/arch/conftest.py`'s `container_override(app, "redis", ...)` seam
  (outside this file set; see *Rollout*).

## Interface contracts

Owning module: `voiceai/modules/health/` plus its composition in
`voiceai/core/container.py` (only the health wiring and its builder).

Files touched: `voiceai/core/container.py`, `voiceai/modules/health/repository.py`,
`voiceai/modules/health/{README,CONTRACT,RUNBOOK}.md`,
`voiceai/modules/health/tests/test_controller.py`,
`tests/arch/core/test_container.py`, `tests/arch/test_redis_isolation.py`.

### Container

```python
health_repository = providers.Factory(
    _build_health_repository, cache_client=redis_cache, db_client=db_client
)
```

- `_build_health_repository(cache_client, db_client)` hands the cache client to
  `HealthRepository(redis_client=cache_client, db_client=db_client)`.
- `redis_cache` is a `Singleton`, so the probe pings the very object the auth store
  holds — no second client, no second connection pool.

### Routes (unchanged shapes, corrected truth)

| Route | Redis cost | `redis` component |
| --- | --- | --- |
| `GET /api/v1/health` | one `PING` on the cache client | `up` / `down` / `skipped` |
| `GET /api/v1/health/ready` | one `PING` on the cache client | same; `down` answers 503 |
| `GET /api/v1/health/live` | none | not reported |

`redis` state by configuration:

| `REDIS_CACHE_URL` | `REDIS_URL` | probed server | state when it answers |
| --- | --- | --- | --- |
| set | empty | cache URL | `up` (was `skipped`) |
| set | set (different) | cache URL | `up` (was: pinged `REDIS_URL`) |
| empty | set | `REDIS_URL` (fallback) | `up` (unchanged) |
| empty | empty | none | `skipped` (unchanged) |

### `container.redis_client` — the legacy single-URL alias

After this spec `redis_client` (`create_redis`, `REDIS_URL` only) has **no
production consumer**: the only resolutions left under `voiceai/` are its own
declaration and `aclose_container` (which closes whatever is bound). It is kept
because tests override and resolve it (`tests/arch/conftest.py`'s `"redis"` key,
`tests/arch/core/test_container.py`, `tests/arch/core/test_app_factory.py`). New
code must depend on `redis_cache`; nothing may be wired to `redis_client` again.

## Findings

Recorded for the integrator; not fixed here (outside the file set).

1. **The shared login throttle is not wired.** `README.md`, `.env.sample`,
   `local_setup/README.md` and `voiceai/platform/RUNBOOK.md` say `REDIS_CACHE_URL`
   backs a "shared login throttle + JWT denylist". The container's
   `_build_auth_service` builds `AuthService(auth_store, jwt=jwt)` with no
   `limiter`, so every process uses the in-process `LocalLoginLimiter`;
   `modules.auth.utils.RedisLoginLimiter` has no production construction site. In a
   multi-worker deployment the login window is per process, not shared. The fix is
   `AuthService(auth_store, jwt=jwt, limiter=RedisLoginLimiter(cache_client))` with
   `redis_cache` injected — but that adds `INCR` (+ `EXPIRE` on the first hit) per
   login/signup to the Redis budget, which `test_full_password_flow_costs_four_cache_touches`
   pins by design, so it needs an owner decision and its own spec. Until then the
   docs overstate what Redis does; the denylist claim is accurate.
2. **Spec 0048's review note (item 7) describes this fix as landed.** It says the
   container already probes `redis_cache` and that `container_override(app, "redis",
   fake)` binds `redis_cache`. Neither was in the working tree: the container change
   lands here; the conftest mapping still binds `redis_client` (see *Rollout*).

## Data model

N/A — no collections, fields or indexes change. The database probe row
(`health_checks`, `id="health-probe"`) is untouched.

## Security notes

- No new route, no authN/Z change: the health routes stay public and unauthenticated.
- The payload never carries a URL, host or exception text: details stay the generic
  constants in `modules/health/constants.py` (`redis not configured`,
  `redis ping failed`), and the failing-ping test keeps asserting that driver text
  does not reach the response (AGENTS.md §4).
- Readiness now tells the truth about the dependency that gates token revocation.
  A cache outage still fails **open** at the auth store (the user row's
  `token_version` is the durable check); the probe makes that degraded state visible
  instead of hiding it.
- Redis budget (Upstash quota incident regression): unchanged at one `PING` per
  report/readiness call, zero for liveness. The ping moves from the legacy client to
  the cache client; it is never doubled.
- Secrets: URLs stay env-only and are excluded from the container's startup log
  (`_SENSITIVE_ENV_FIELDS`), unchanged.

## Test plan

Offline only (no socket is opened: a constructed client's `ping` is replaced on the
instance, or the provider is overridden with a double).

- `tests/arch/core/test_container.py` (`TestHealthProbesTheCacheClient`), through the
  real factories:
  - cache URL only (`REDIS_URL` empty) → the probe pings the cache client, `UP`
    (the reproduction above, red before the fix);
  - split URLs → the cache client is pinged, the legacy client is not;
  - legacy URL only → the fallback cache client is pinged, the legacy alias is not;
  - no URL → `SKIPPED`, nothing built;
  - an override of the legacy alias alone is not probed.
- `tests/arch/test_redis_isolation.py`:
  - `test_configured_health_costs_exactly_one_ping` counts on `redis_cache` (the
    client it counted on changed; the cost — exactly one ping, none for liveness —
    did not);
  - new: readiness costs exactly one ping on the cache client;
  - new: a double bound only to the legacy alias sees zero calls and the component
    reads `skipped`;
  - `test_full_password_flow_costs_four_cache_touches` and the unconfigured tests
    are unchanged and stay green.
- `voiceai/modules/health/tests/test_controller.py`: the unready fixture injects the
  failing double as the cache client; new tests pin `redis: up` through the cache
  client over HTTP and that a legacy-only client leaves readiness at 200/`skipped`.
- `voiceai/modules/health/tests/test_repository.py`, `test_service.py`: unchanged
  (the repository's probe logic is untouched).

## Verification

```sh
.venv/bin/python -m pytest -q voiceai/modules/health tests/arch/core/test_container.py tests/arch/test_redis_isolation.py -p no:warnings
.venv/bin/python -m pytest -q tests/arch -p no:warnings
make lint-arch
make type
make sec
```

## Rollout

No flag: the old wiring reports a falsehood, there is nothing to dual-serve.
Rollback is revert.

Operator-visible change: a deployment with `REDIS_CACHE_URL` set and `REDIS_URL`
empty moves from `redis: skipped` to `redis: up` — or to `down` and a 503 on
`/api/v1/health/ready` if that URL is wrong or the cache is unreachable. Check the
cache URL before rolling out behind a load balancer that gates on readiness; the
container healthcheck uses `/live` and is unaffected.

Follow-ups (outside this batch's file set):

- `tests/arch/conftest.py`: `_KEY_TO_PROVIDER[CONTAINER_KEY_REDIS]` still maps to
  `redis_client`. It should map to `redis_cache` (the one Redis the app uses), with
  `tests/arch/core/test_container.py::test_container_override_fixture_swaps_a_built_dependency`
  and the two `tests/arch/core/test_app_factory.py` shutdown tests asserting on
  `redis_cache` in the same change. Until then tests that need the health probe's
  client override `container.redis_cache` directly (as this spec's tests do).
- Wire `RedisLoginLimiter` over `redis_cache` (Finding 1) under its own spec, or
  correct the four documents that promise a shared throttle.
- Retire `container.redis_client` and `core.redis.create_redis` once the conftest
  seam has moved.

No legacy shims.

## Burn-down

- [x] Spec written before code.
- [x] Reproduction observed (2026-10-01: cache URL only → `redis_client()` is `None`,
      `probe_redis()` → `SKIPPED "redis not configured"`).
- [x] `voiceai/core/container.py`: `health_repository` built from `redis_cache`.
- [x] `voiceai/modules/health/repository.py` docstrings + module
      `README.md`/`CONTRACT.md`/`RUNBOOK.md` name the cache client.
- [x] `tests/arch/core/test_container.py`: `TestHealthProbesTheCacheClient`.
- [x] `tests/arch/test_redis_isolation.py`: accounting moved to the cache client,
      legacy-alias and readiness cases added.
- [x] `voiceai/modules/health/tests/test_controller.py`: cache-client seam.
- [x] Gates green (2026-10-01): `tests/arch` + `voiceai/modules/health` 622 passed,
      `make lint-arch`, `make type` (531 files), `make sec`.
- [ ] Follow-ups in *Rollout* (conftest seam, shared login throttle, retiring
      `redis_client`) — outside this batch's file set.
