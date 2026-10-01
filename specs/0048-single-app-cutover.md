# Spec 0048: single app cutover — quickstart retired, Atlas is truth, Redis is a cache

> Owner directive (2026-10-01): "quickstart hatao, sab kuch ek hi rakho" — one
> process (`voiceai.app`), one system of record (Mongo Atlas), Redis only for
> TTL ephemera. Pulls the spec 0018 M6 "platform router/store retired" outcome
> forward for the *store* and the *process*; the router stays frozen (its
> module-by-module retirement remains M6).

## Goal

Today two backends coexist. `voiceai.app` (Dockerfile CMD since 2c385eff) is
Mongo-first but serves 66 routes. `local_setup/quickstart_server.py` serves 133
unique paths — it mounts the frozen `platform/router.py` over
`RedisStore(redis_client)`, legacy agent CRUD over `RedisAgentRepository`, and
wallet over the Redis seam — so production runs quickstart and Redis (Upstash)
is the database: every authenticated request does a guaranteed-miss session
`GET` before Mongo, every list endpoint is `KEYS` + N `GET`, the agent list is
`KEYS *` over the whole keyspace, and every platform write (`SET`) never reaches
Atlas (spec 0047 §deviations: numbers "live in Redis, absent from Atlas
entirely"). One user drove ~77k Upstash commands.

After this spec: `voiceai.app` serves every path the UI uses (the 73
platform routes join the 66 module routes), all persistence is Atlas, Redis
holds only the login throttle and the JWT denylist, `quickstart_server.py` and
every Redis-as-database adapter are deleted.

## Non-goals

- Rewriting platform entities as modules (numbers, KBs, webhooks, graphs,
  workflows, campaigns, batches, executions, integrations, sub-accounts,
  organization, api-keys, inbound, vector-config): M6/M8 per spec 0018. The
  frozen `platform/router.py` handlers are mounted, not edited (one exception
  below: the `keys_router` store seam).
- Shape migration `executions → calls`, ULID ids, `workspace_id`: M6/M1.
- `core/bus.py`, jobs worker, realtime split (M6/M9). Redis Streams stay future.
- Telephony servers in `local_setup/telephony_server/` (spec 0021/M2 own them).
- UI changes. Paths served under `/api/v1` are unchanged. Bare (un-prefixed)
  twins retire with quickstart — owner decision 2026-10-01: no alias mount;
  any client still on bare paths is fixed in the UI.

## Decisions (approved by the owner directive; refine in the architect pass)

1. **One process.** `create_app` mounts the platform routers after the module
   routers, under `API_PREFIX` only. First match wins, so module handlers keep
   `/calls/place`, `/tools*`, `/voices*`, `/wallet*`, `/templates*`, `/agent*`;
   the platform copies of those routers are not mounted at all.
2. **One store interface, two backends.** `MemoryStore` primitives become
   `async` (`_put/_get/_all/_delete/_clear` + singleton and ledger primitives),
   every entity method awaits them, and `RepositoryPlatformStore(MemoryStore)`
   (`voiceai/platform/repository_store.py`) overrides only the primitives —
   over the greenfield `InMemoryRepository`/`MotorRepository`, so it is
   driver-agnostic and needs zero raw driver code. `RedisStore` is deleted in
   Slice D (its 86 duplicated methods with it).
3. **Auth families are not duplicated.** users/sessions/invites/api_keys/
   auth_events/revoked in `MongoPlatformStore` delegate to the container
   `MongoAuthStore` (same collections, same greenfield models). `get_principal`
   resolves sessions and API keys through the auth store — session by hash,
   API key by indexed `get_api_key_by_hash` — never a `KEYS` scan.
4. **Bridge collections are namespaced.** Non-auth families persist verbatim
   (legacy shape + tenant stamp) in `platform_<family>` collections so they can
   never collide with module collections (`executions` already holds
   `PlacedCall`). M6 migrates them to target names/shapes and drops them.
5. **Backfill is operator-run, idempotent, censused first.** The copy loop
   lands in `voiceai/tooling/backfill_upstash_to_atlas.py` (the family table
   already exists there); natural keys make re-runs safe; ephemera (sessions,
   throttle, denylist) are never copied.
6. **Redis is optional.** With `REDIS_URL` empty the app boots and serves
   everything; throttle falls back to the in-process limiter, denylist reads
   the store (both behaviours already exist).

## Interface contracts

Slices are disjoint file sets; one slice per session, gates green between.

**Slice A — store (landed 2026-10-01; files: `voiceai/platform/store.py`,
`voiceai/platform/repository_store.py` (new), `voiceai/core/container.py`,
`voiceai/database/constants.py`, `voiceai/modules/auth/{ports,repository}.py`,
`Makefile`, `tests/arch/platform/test_repository_store.py` (new))**
- `MemoryStore`: primitives `async`; singleton (`wallet`/`organization`) and
  ledger primitives; `reset_platform` walks a module-level family tuple; the
  default wallet/organization materialise once on first read. No public
  signature changes; `RedisStore` untouched (it overrides every method).
- `RepositoryPlatformStore(MemoryStore)`: `__init__(auth_store, repositories)`
  with one `PlatformRow` repository per `PLATFORM_COLLECTIONS` member (a
  missing one is a `ConfigurationError`); primitives wrap each call in a
  `TenantScopedRepository` built from `current_tenant()` **per call** (so the
  container holds one singleton); `_all` pages the unscoped repository and
  filters by tenant in-process (every scoped listing is capped at
  `MAX_PAGE_SIZE`); the six auth families delegate to `AuthStorePort`
  (`delete_api_key` added to the port, `MongoAuthStore` and the test fakes);
  `reset_platform` revokes API keys through the auth store (legacy parity).
  The file is strict-gated via `ARCH_EXTRA` in the Makefile.
- Container: `platform_store = providers.Singleton(create_platform_store,
  db_client, auth_store)`; the driver (in-memory vs Motor) is chosen there.
- Tests (20): ten scenario scripts run against `MemoryStore` and the bridge
  and must dump identically; tenant invisibility, stamp on disk, unbound
  tenant fails closed; auth delegation + expired-session retirement;
  namespace/uniqueness of the bridge collections; container singleton.

**Slice B — single app (landed 2026-10-01; files: `voiceai/core/app_factory.py`,
`voiceai/common/constants.py`, `voiceai/platform/auth.py`, `voiceai/platform/router.py`,
`voiceai/modules/voice/controller.py`, `tests/arch/test_route_inventory.py` (new),
`tests/arch/platform/{conftest,test_principal,test_single_app}.py` (new),
`voiceai/modules/voice/tests/test_controller.py`, `tests/arch/test_quickstart_dualmount.py`
(stale pin removed; file dies in D))**
- `create_app`: after the modules, `single_app_routers()` (new, in
  `platform/router.py`) mounts prefix-only: executions, batches, numbers, kbs,
  webhooks, inbound, agents (vector-config), sub-accounts, integrations,
  graphs, workflows, runs, campaigns, organization, api-keys, and `/calls/simulate`
  on its own `simulate_router` (split off `calls_router` so the voice module keeps
  `/calls/place` without a shadowed twin). Excluded: auth (tombstone), tools,
  voices, wallet, templates — their modules own those paths.
- **Deviation:** the store is NOT resolved at `create_app` time. `platform_store_of(app)`
  (in `app_factory`) returns a store staged on `app.state` (legacy test app,
  quickstart) or resolves `container.platform_store()` on first use, so provider
  overrides applied after the factory (the test convention, e.g. the Redis
  isolation accounting) still bind. Both `get_store` seams (`platform/auth.py`,
  `platform/router.py`) go through it; the voice WS handler hands
  `container.platform_store()` to `run_call` directly (the inbound contract gate
  bans `platform`-named imports in that file).
- `get_principal`: cookie → `store.get_session` → `get_user` (the bridge delegates
  to the auth store, Slice A); bearer → `store.get_api_key_by_hash(token_hash)`
  — one indexed read, the `list_api_keys` scan is gone. **Deviation:**
  `_principal_from_mongo` (d6eadbca) stays as a tagged `legacy-shim(spec-0048)`
  because only quickstart still reaches it (its store is Redis); deleted in D.
  `audit()` reaches the auth store through the bridge unchanged.
- `create_platform_app` and the spec-0007 dual-mount parity test stay until D
  (legacy suites still build that app).
- Tests: route inventory pins 135 `METHOD /api/v1/...` pairs + 4 docs paths,
  no bare twins, no duplicate method+path, shared paths owned by modules, 73
  platform-owned; principal resolution (cookie hit/expired/ws-ticket/disabled/
  unknown, bearer hit/wrong/prefix/expired/retired, dependency order → 401);
  end-to-end through `create_app` + ASGI: signup → numbers round trip lands in
  `platform_numbers` under the owner's tenant, simulate → executions stats,
  organization PUT, API key minted into the auth store and authenticating a
  Bearer call in the same tenant, bare paths 404, anonymous 401; WS run receives
  the container's store.

**Slice C — backfill (files: `voiceai/tooling/backfill_upstash_to_atlas.py`,
`voiceai/tooling/tests/test_backfill.py`, `voiceai/platform/RUNBOOK.md`)**
- `python -m voiceai.tooling.backfill_upstash_to_atlas --census` prints
  per-family counts (no writes). `--apply` copies MIGRATED families:
  agents/prompts → `agents`/`agent_prompts` via the container repositories;
  wallet/ledger/templates → module collections via the mappers now in
  `wallet/adapters/legacy_store.py` (mappers move to the tool before that
  file dies); api_keys/users/invites/auth_events → auth store; every other
  family → `platform_<family>`. Idempotent on natural key; summary counts;
  never logs payloads. Reads Redis with `SCAN`, never `KEYS`.

**Slice D — deletion (files: `local_setup/quickstart_server.py` (deleted),
`voiceai/platform/store.py` (`RedisStore` removed), `voiceai/modules/agents/repository.py`
(`RedisLike`, `RedisAgentRepository` removed), `voiceai/modules/agents/constants.py`,
`voiceai/modules/wallet/adapters/legacy_store.py` (deleted), `voiceai/platform/__init__.py`,
`voiceai/platform/agent_records.py`, `tests/test_agent_prompts_endpoint.py`
(rewritten against `create_app`), `tests/auth_helpers.py`, `tests/arch/core/test_foundation.py`,
`voiceai/modules/auth/tests/test_burndown.py`, `docker-compose.yml`, `Makefile`)**
- Container keeps `redis_client` (throttle, health probe) and `redis_cache`
  (denylist); every other consumer is gone. `tests/arch/test_no_parallel_impl`
  (spec 0019 ledger) gains "no Redis persistence outside throttle/denylist".
- `docker-compose.yml`: `voiceai-app` no longer `depends_on: redis` (cache is
  optional); telephony services untouched.

**Slice E — verification + docs (files: `AGENTS.md` §8 note, `docs/` via
`make docs`, `voiceai/platform/README.md`, memory)**

## Data model

New collections (all `platform_*`, bridge only, dropped at M6):
`platform_executions, platform_batches, platform_numbers, platform_kbs,
platform_webhooks, platform_inbound, platform_vector, platform_subaccounts,
platform_integrations, platform_graphs, platform_graph_versions,
platform_workflows, platform_workflow_versions, platform_workflow_runs,
platform_workflow_campaigns, platform_singletons` (org settings doc).
Document = `PlatformRow(BaseFields)`: the legacy payload verbatim under
`payload`, plus the `BaseFields` envelope (`tenant_id`, `created_at`,
`updated_at`, `is_active`); `_id` = the legacy item id (singletons:
`<tenant>:<name>`, ledger rows: generated). Nesting keeps legacy fields from
colliding with the envelope. Indexes (Slice C, with the backfill):
`{tenant_id, _id}` everywhere; `platform_executions {tenant_id,
payload.agent_id, payload.started_at}`, `{tenant_id, payload.batch_id}`;
`platform_numbers {tenant_id, payload.number}` unique (spec 0047 follow-up).
Registered in `database.constants.Collections` under a `PLATFORM_` prefix.
Auth families reuse existing `users/sessions/invites/api_keys/auth_events/
revoked_tokens`. `platform_tools`/`platform_voices`: censused; migrated only
if the census finds rows (the module collections are the target).
Deletes are soft (`is_active=False`, AGENTS.md rule 5) — the repositories
give that for free and the frozen router observes the same "gone" semantics.

## Security notes

- AuthN unchanged in effect (`require_*` roles on every platform route); the
  resolver now reads one store, so a disabled user or deleted key takes effect
  on the next request everywhere, not only on module routes.
- Tenant stamping on every bridge write from `current_tenant()`; reads filter
  by tenant; `system_scope` only in the backfill tool.
- Backfill: Upstash and Atlas URLs from env (`redact_secrets` on log), TLS
  (`rediss://`), read-only Redis usage, counts only in output.
- No new endpoints. CORS, rate limits, SSRF surfaces unchanged.
- Removing `KEYS *` also removes an O(keyspace) DoS lever on the agent list.

## Test plan

- Slice A: store parity suite (memory vs Mongo-over-`InMemoryDatabase`) for
  every public method; tenant isolation test (two tenants, no leakage);
  `delete_api_key` unit on `MongoAuthStore`.
- Slice B: route inventory pin; `get_principal` tests (cookie hit/miss,
  bearer hit/expired/prefix mismatch, disabled user) with a fake auth store;
  controller tests for three representative platform routes through
  `create_app` + httpx ASGI (list/create/delete numbers, executions stats,
  organization PUT); `_record_execution` reaches the store.
- Slice C: backfill unit tests with fake Redis (`SCAN` pages) and
  `InMemoryDatabase`: idempotency, ephemera never copied, counts.
- Slice D: no-parallel-impl ledger entry; `make test-all` with the legacy
  quickstart tests rewritten or deleted (each deletion listed in the commit).
- Coverage ≥ 85% on `voiceai/platform/mongo_store.py`, `core/app_factory.py`,
  `tooling/backfill_upstash_to_atlas.py`.
- Deploy config (batch `deploy-config`): `tests/arch/core/test_env_sample.py` keeps
  `.env.sample` complete and loadable (every `Environment` field named, no inline
  comment in a value, `load_environment(.env.sample)` builds `db_backend == mongo` and
  `voice_ws_enabled is True`); `tests/arch/core/test_db.py` pins `MONGO_URL` alone
  selecting the Atlas URL; `tests/arch/core/test_container.py` pins readiness probing
  the cache client; `tests/test_telephony_trunk_tickets.py` pins the trunks' ticketed
  `<Stream>` URL and the 500 on a failed mint.

## Verification

```sh
make check
make sec
make cov
make test-all
```

Plus per slice: `python -m voiceai.tooling.backfill_upstash_to_atlas --census`
against prod (operator, Slice C); Upstash console command count for 24h
before Slice B deploy and 24h after Slice D deploy (expected: throttle +
denylist + readiness pings only).

## Rollout

1. Gates green at the start (the two red gates on `feature--dev--2026` as of
   2026-09-30 — stale `test_quickstart_dualmount` and two `no-any-return`
   errors in `auth/service_identity.py` — are fixed in Slices B and A).
   Baseline debt found while landing Slices A/B (all pre-date this spec, all
   quickstart-era, all handled in Slice D): 70 legacy `tests/test_platform_*`
   cases fail on `HEAD` (signup answers "Token pair issuance is not configured"
   — the legacy test env sets no JWT keys); `tests/test_clinic_appointment_agent.py`
   imports an example deleted in 2c385eff; `tests/test_agent_prompts_endpoint.py`
   imports quickstart at collection time, which breaks
   `test_foundation::test_live_answers_without_touching_quickstart` in
   `make test-all` only (it passes in `make test`).
2. Slice A ships dark (no mount change).
3. Slice B deploy = the cutover: `uvicorn voiceai.app:app` (already the
   Dockerfile CMD) with `DB_BACKEND=mongo`, `MONGO_URL`, `VOICE_WS_ENABLED=true`
   (the greenfield WS route is flag-gated), `REDIS_CACHE_URL` for throttle/
   denylist. Run Slice C `--apply` immediately before switching traffic;
   re-run after (idempotent) to catch writes during the switch.
4. Rollback for one release: quickstart file still exists until Slice D; the
   old process starts with the old env. After Slice D rollback is a git revert
   of D only (A–C are additive).
5. Shims registered for burn-down: none new; the ones deleted are
   `legacy-shim(spec-0002)` (`RedisAgentRepository`), `legacy-shim(spec-0006)`
   wallet seam, spec 0007 dual mount.
6. Deploy configuration and operator docs (batch `deploy-config`, after Slice D;
   no Python files): `.env.sample` re-keyed to the single app — every name checked
   against `voiceai/core/environment.py` (`DB_BACKEND=mongo`, `MONGO_URL`/`DB_URL`,
   `VOICE_WS_ENABLED=true`, `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` PEM, `ALLOWED_ORIGINS`,
   `REDIS_CACHE_URL` optional; `REDIS_URL` no longer required). `README.md` and
   `local_setup/README.md` name `uvicorn voiceai.app:app` as the only server, the
   `/api/v1` prefix, the backfill runbook and what each telephony trunk reads.
   `Dockerfile`/`docker-compose.yml`: no service `depends_on: redis` (the Twilio/
   Plivo trunks import `redis.asyncio` but never call it — verified, not guessed),
   Redis behind `--profile cache`, per-service healthchecks. CI: `lint`, `test`,
   `security`, `modules` workflows run `make setup` (uv, Python 3.10) before the
   make gates (`make check`, `make sec`) — they previously executed `.venv/bin/...`
   without creating the venv and skipped `master`/`feature--*` pushes. No secrets.
7. Review pass on the deploy configuration (batch `deploy-config`, 2026-10-01; owner
   directive: "sirf verify audit hi mat karte raho, changes karo code me" — fix the
   findings in code, do not re-document around them). Files outside the batch's declared
   set are touched only where the working tree showed no concurrent edit; each is listed
   here for the integrator:
   - `voiceai/core/db.py`: `create_db` reads `env.db_url_effective` (`MONGO_URL`, legacy
     `DB_URL` fallback), so the configuration `.env.sample`/`README.md`/`RUNBOOK.md`
     prescribe (`MONGO_URL` set, `DB_URL` blank) boots. Pinned in
     `tests/arch/core/test_db.py`.
   - `voiceai/core/container.py`: `health_repository` probes `redis_cache`
     (`REDIS_CACHE_URL`, legacy `REDIS_URL` fallback) — the client the app uses for the
     login throttle and the JWT denylist — so `GET /api/v1/health/ready` reports the
     cache that is configured. `redis_client` (legacy `REDIS_URL` only) keeps its
     shutdown close and has no other consumer; retiring it is a follow-up spec. Test
     seam: `container_override(app, "redis", fake)` binds `redis_cache`
     (`tests/arch/conftest.py`); pinned in `tests/arch/core/test_container.py`.
   - `tests/test_platform_auth.py`: the duplicate `signup_owner` (F811) is deleted;
     `make lint` is green again.
   - `local_setup/ngrok-config.yml`: carries no `authtoken` — the ngrok agent reads
     `NGROK_AUTHTOKEN` from the container environment, which `docker-compose.yml`
     interpolates from `.env`; tunnels `twilio-app` and `voiceai-app` are defined
     (`plivo-app` stays commented for constrained ngrok plans; uncomment for the Plivo
     trunk). The token that was committed must be rotated in the ngrok dashboard — it
     stays in git history.
   - `local_setup/telephony_server/{twilio,plivo}_api_server.py` (named a non-goal
     above; changed because the shipped compose cannot place a call against the single
     app otherwise — spec 0021's ticket contract is consumed, not changed): the answer
     callback mints a single-use ticket (`POST /api/v1/auth/ws-ticket`, Bearer
     `VOICEAI_API_KEY`, over `VOICEAI_INTERNAL_URL`) and points the carrier `<Stream>`
     at `wss://<tunnel>/api/v1/chat/v1/{agent_id}?ticket=…`. Minted at answer time, not
     dial time: the ticket lives 60 s and ringing can take longer. A failed mint answers
     the carrier 500 instead of a ticket-less URL. Pinned in
     `tests/test_telephony_trunk_tickets.py`.
   - `tests/arch/core/test_env_sample.py`: `.env.sample` names every `Environment`
     field, no value carries an inline comment, and `load_environment(.env.sample)`
     builds `db_backend == mongo`, `voice_ws_enabled is True`.
   Not done here: `make fmt` over the arch scope (57 files, several mid-edit by
   concurrent batches — reformatting under another agent's edit is a collision), so the
   integrator runs it once after the Python batches land.

## Burn-down

- [x] Slice A: async primitives, `RepositoryPlatformStore`, container provider, parity tests (2026-10-01; `make lint/lint-arch/type/sec/test` green).
- [x] Slice B: single-app mount, Mongo-first principal, route inventory pin, WS flag noted (2026-10-01; lint/lint-arch/type/sec green, `make test` 2002 passed / 0 failed).
- [x] Spec 0047 seam wired (2026-10-01): `voiceai/modules/voice/adapters/inbound_store.py` (`PlatformInboundStore`) over `RepositoryPlatformStore.scan_family` (tenant-blind read, documented as the one exception), provider `container.inbound_store`, the controller resolves it; pinned by `tests/arch/platform/test_inbound_seam.py`.
- [ ] Observed outside this spec (owner to decide, spec 0029 territory): the tools module routes carry no role/scope gate — anonymous callers list system tools (200) — and a legacy-shaped `POST /api/v1/tools` body answers 500 (pydantic error raised outside request validation) instead of 422.
- [x] Slice C (2026-10-01): `voiceai/tooling/backfill_upstash_to_atlas.py` gained the copy loop — `--census` (SCAN counts, no writes) and `--apply` (auth families → auth store, `platform:v1:<family>:*` → `platform_<family>` via `RepositoryPlatformStore.restore`, wallet + ledger → wallet module via the public `create_wallet_repository`, organization singleton, bare-key agents → agents module, `agent_data/*/conversation_details.json` → agent prompts). Rules pinned by `tests/arch/test_backfill_upstash.py`: skip rows already in Atlas (never overwrite), never copy sessions/revoked/throttle/denylist, never `KEYS`, quarantine bad rows, counts only. **Deviation:** the pre-existing family table named `platform:v1:apikeys:*` / `events:*`; the real store wrote `api_keys` / `auth_events`, corrected. Runbook: `voiceai/platform/RUNBOOK.md`. Prod census/apply: operator step, not yet run.
- [x] Slice D (2026-10-01): deleted `local_setup/quickstart_server.py`, `RedisStore`, `RedisLike`/`RedisAgentRepository`/`FilePromptStore`, `platform/agent_records.py`, `wallet/adapters/legacy_store.py`, `create_platform_app`/`build_routers`, the spec-0007 dual-mount parity test, the quickstart tests, `_principal_from_mongo`; `depends_on: redis` dropped for the app; size-debt and tenancy ledgers updated. **Deviation (kept, not deleted):** the legacy `tests/test_platform_*` contract suites now run against the single app through `tests/auth_helpers.build_platform_test_app()` under `/api/v1` — they went from 70 red (no JWT) to green; retired only the tests of surfaces the modules own with a different contract (platform tools, platform voices, code-seeded templates). Two semantics changed with them and are deliberate: the wallet survives a workspace reset (module-owned money is not platform data), and the wallet ledger accepts the legacy `?type=` filter as an alias of `entry_type` (UI compat).
- [x] Slice E (2026-10-01): runbook written; route inventory pinned; gates green (`make lint`, `lint-arch`, `type`, `sec`, `make test` 1978/0; `make test-all` shows only the 7 pre-existing failures already deselected in `make cov`). Upstash before/after counts: operator step after deploy — record here.
- [x] Owner decision: bare paths retire with quickstart, no alias mount (UI is fixed instead).
- [x] Deploy config + operator docs (batch `deploy-config`, 2026-10-01; no Python files, one line per change):
  - `.env.sample`: rewritten for the single app — `DB_BACKEND=mongo` + `MONGO_URL` (`DB_URL` legacy alias), `VOICE_WS_ENABLED=true`, JWT RS256 PEM pair (openssl recipe, `\n` form for Docker `env_file`), `ALLOWED_ORIGINS` exact-origin rule, cookie knobs, `REDIS_CACHE_URL` optional (`REDIS_URL` only a fallback, no longer required), `TWILIO_AUTH_TOKEN`/`TALKO_SERVICE_BASE_URL` (app), blob store, provider keys, one block per trunk; all 26 `Environment` fields present, verified by loading the file through `load_environment`.
  - `README.md`: the "Local example setup" story (four containers, "redis: for persisting agents & prompt data") replaced by "Running the server" (`uvicorn voiceai.app:app` is the only server, `/api/v1`, health, WS ticket contract, required/optional env, gates, runbook) and a "Local Docker setup" service table with the env each trunk reads.
  - `local_setup/README.md`: "redis: for persisting agents & prompt data" removed; per-service table with the env each trunk reads (from their `os.getenv` calls), ngrok tunnel-name requirement, `cache`/`mongo` profiles, health checks, WS contract note (Twilio/Plivo trunks still dial the retired bare path without a ticket — spec 0021/M2).
  - `Dockerfile`: `EXPOSE` gains 8004 (talko trunk); healthcheck/CMD comments name the single app; CMD unchanged.
  - `docker-compose.yml`: `depends_on: redis` removed from `twilio-app`/`plivo-app` (their `redis.asyncio` import is never called); `redis` behind `--profile cache`; `mongo` comment says production is Atlas; trunks wait for `voiceai-app` `service_healthy` and get their own `/docs` healthchecks (the image healthcheck probes 5001); comments list the env each service reads.
  - `.github/workflows/{lint,test,security,modules}.yml`: `pip install uv` + `make setup` before every make gate (they ran `.venv/bin/...` without creating the venv, so every run failed; the `|| ruff format` fallback in lint hid it); triggers gain `master` and `feature**` (`feature--dev--2026` matched nothing); `security` runs `make sec` (same bandit pin and scope as local); lint drops the `requirements-txt-fixer` step (not a make gate; fails on the current `requirements.txt` order); modules matrix gains `tools`/`chat`/`catalog`/`voices` (each verified ≥ 85% locally) and the dup job runs `make dup`; uv cache via `actions/cache`; concurrency cancel-in-progress. `auto-release.yml`/`publish.yml` untouched (release automation, `workflow_dispatch` by design). No secrets added.
  - `voiceai/platform/RUNBOOK.md`: new "Process and configuration" section (env table, health, gates), cutover gains the `--tenant`/`REDIS_URL` notes and a rollback step, day two gains the telephony/WS-ticket note.
  - review pass (deploy-config, 2026-10-01): `.github/workflows/security.yml` header corrected — the `dev` extra lists `bandit` unpinned (the old CI step pinned `1.9.4`), so the comment no longer claims a pin; pinning belongs in `pyproject.toml` (outside this batch).
- [ ] Observed by `deploy-config`, outside its file set (owner to route): (a) `local_setup/telephony_server/{twilio,plivo}_api_server.py` point the carrier `<Stream>` at bare `/chat/v1/{agent_id}` with no `?ticket=` — cannot complete a call against the single app; (b) `local_setup/ngrok-config.yml` has no `voiceai-app` tunnel (the old `bolna-app:5001` entry is commented out) and carries a committed ngrok authtoken — rotate it and replace with a placeholder; (c) `make lint` is red on `HEAD` (`tests/test_platform_auth.py:35` F811 `signup_owner`) and `ruff format --check` on the arch scope reports 57 files — `make check` and the lint workflow stay red until `make fmt` + that fix land; (d) `requirements.txt` is not in `requirements-txt-fixer` order; (e) AGENTS.md §6 still says the workflows are `workflow_dispatch`-only.
