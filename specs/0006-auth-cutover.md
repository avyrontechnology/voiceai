# Spec 0006 — Auth cutover (endgame for tranche C)

- **Status:** done (E1→E4 landed; legacy `/auth` dark, shims collapsed, spec closed)
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** spec 0005 (done — controller proven at `/api/v1/auth`, legacy frozen)

## Goal

Retire the legacy `/auth` serving path so one controller owns authentication: the
deployed entry serves the new controller, the store resolves through the container
instead of `app.state`, the login limiter is shared across workers, and the legacy
auth files (`platform/auth.py`, `platform/auth_router.py`, the model shim rows, the
store auth methods) shrink to deletion. External clients migrate from bare shapes
to the standard envelopes during a dual-mount transition, then `/auth` goes dark.

## Non-goals

- The rest of `platform/` (graphs, batches, wallets, integrations stay legacy —
  later specs; `build_routers()` keeps serving them throughout).
- Quickstart's non-auth routes (agent CRUD keeps its legacy auth deps until this
  spec migrates them — see step E2).
- Changing auth behavior: statuses, `detail` strings, `data` payloads, cookie flags
  and TTLs are frozen by spec 0005 (the C5 suite is the regression net).

## Design

Three coupled retirements (one spec because each needs the others — the controller's
per-request construction, the app.state seam and the per-process limiter all retire
together):

- **E1 — Container store wiring.** Register the store in the container
  (`core/container.py::build_container` + `modules/auth/__init__.py::register`):
  `AuthStorePort` → `MemoryStore` (tests) / `RedisStore` (deployed, over the
  container redis). Controller dependencies resolve `AuthService` from the
  container (rule 9 end-state); `get_store`'s app.state read becomes the 503
  fallback only until E3 deletes it. `modules/voice/service.py`'s
  `platform_store` kwarg (kwargs-injection bridge, §3.1-3) rebinds to the same
  registration — no signature change.
- **E2 — Quickstart dual-mount.** Quickstart mounts the new controller alongside
  the legacy router: bare shapes stay at `/auth`, envelopes at `/api/v1/auth`
  (prefixes already differ — no collision). Quickstart's own agent-CRUD routes
  swap `platform.auth` deps (`require_scope`, `get_principal`, `Principal`) for
  the module equivalents behind the container service; `_authorize_voice_socket`
  resolves tickets through `AuthService.redeem_ticket`. Legacy `auth_router` stays
  mounted until external clients confirm the enveloped paths, then unmounts.
- **E3 — Shared login limiter.** `modules/auth/utils.py` gains a container-backed
  sliding window (redis `INCR`+`PTTL` over the container client, same 5/min/IP),
  falling back to the in-process ledger when redis is absent (tests, single-proc
  dev). `AuthService.login` takes the limiter through its constructor (default
  keeps today's behavior so the C3 suite passes unchanged); `check_login_allowed`
  stays as the local implementation behind the seam.
- **E4 — Shim deletion (DONE).** `/auth` unmounted from quickstart (sunset
  middleware retired with it); `auth_router.py` is a 5-line tombstone (object +
  docstring, zero routes); `auth.py` collapsed to the principal chain still
  imported by frozen `platform/router.py` (all else grep-verified dead);
  `models.py`/`store.py` fully retained (every row proven live — port members or
  other-router imports). Burn-down rewritten to the absence set.
  - Legacy test fallout (integrator): 8 platform test files repointed to
    `/api/v1/auth` via test-side `mount_new_auth` (mount + container + factory
    error handlers — legacy source untouched); `test_platform_auth.py` rewritten
    to envelopes (same statuses/details/payloads); dual-mount E2 pins converted
    to /auth-dark assertions. Full suite: same-8 baseline, 2863 passed.

## Interface contracts

- `Container`: `register(AuthStorePort, provider)` in `build_container`
  (environment selects `MemoryStore` vs `RedisStore`); `resolve(AuthStorePort)`.
- `AuthService(store, *, limiter=local_limiter)`: new optional second ctor param,
  default preserves C3 behavior byte-for-byte.
- `LoginLimiter` protocol (new, `modules/auth/ports.py`):
  `async def check(self, ip: str) -> None` raising `TooManyAttemptsError`.
- Routes: no path/shape/status change on either mount during transition; the
  unmount of `/auth` is a separate commit with its own rollout note.
- `GET /api/v1/auth/me` stays the envelope-canary external clients poll to confirm
  migration (200 + `data.user`).

## Data model

None (no schema change; redis limiter keys `auth:throttle:{ip}` with 60s TTL are
ephemeral counters, not persisted models — no `Collections` entry).

## Security notes

- **Envelope migration is the risk:** external clients reading bare `user`/`token`
  fields break silently (200 with a new shape, not an error). Mitigation:
  dual-mount with a dated sunset header on `/auth` responses (`Deprecation: true`
  + `Sunset: <date>`), canary polling of `/api/v1/auth/me`, unmount only after
  the playground confirms.
- **Throttle fallback:** local ledger when redis is down must fail CLOSED or OPEN?
  (architect decides — recommendation: fail open with ERROR log; a redis outage
  must not lock every user out, and abuse pressure is low on this surface).
- **Secrets:** no new secret env vars; redis URL already declared. Cookie flags
  unchanged (bridge retires only when `Environment` owns them — NOT this spec).
- **Error opacity:** unchanged (factory handlers already own it).

## Test plan

- Container wiring: `resolve(AuthStorePort)` returns the env-selected store;
  controller suite runs unmodified against container-resolved service (swap the
  app.state seam for a container fake mid-suite to prove the seam moved).
- Dual-mount: ASGI test asserts both `/auth/me` (bare) and `/api/v1/auth/me`
  (envelope) serve the same user during transition; unmount commit asserts `/auth`
  404s as an envelope.
- Limiter: fake-clock sliding-window unit tests (5 pass, 6th 429s, window reset);
  redis-backed test against the `FakeRedis` conftest double; fallback test with
  no redis (local ledger, warning logged).
- Burn-down updated: absence set asserted (no `auth_router` routes, no delegators).
- Offline only; coverage ≥ 85% on touched packages (unchanged gate).

## Verification

`make check` (ruff + strict arch lint + mypy + `pytest tests/arch`) and `make sec`
green; `make test-all` shows the same-8 baseline; `make cov` ≥ 85%; plus a live
playground login against the dual-mounted quickstart (manual, recorded in the
commit message).

## Rollout

Four commits, each green and revertible: E1 (wiring, no traffic change) → E2
(dual-mount, legacy still primary) → E3 (shared limiter behind flag, local default)
→ E4 (unmount `/auth`, delete shims). Revert of E4 is re-mount (one commit).
External-client migration is the long pole — E4 waits for explicit confirmation,
never rides along with other work.
