# Platform surface runbook (spec 0048)

The frozen `platform/router.py` routes are served by the single app (`voiceai.app`)
over `RepositoryPlatformStore`: every row lives in Atlas under `platform_<family>`
collections, auth rows in the auth store. Redis is an optional cache (login
throttle, JWT denylist). This runbook covers the process configuration, the
one-time cutover from the retired quickstart/Redis deployment, and the day-two
checks.

## Process and configuration

- One server: `uvicorn voiceai.app:app --host 0.0.0.0 --port 5001` — the
  Dockerfile CMD, `docker compose up -d voiceai-app`, or `.venv/bin/uvicorn ...`
  after `make setup`. Every route is under `/api/v1`; bare (un-prefixed) paths
  are gone. `local_setup/quickstart_server.py` no longer exists.
- Environment: every name is declared in `voiceai/core/environment.py`; the
  commented template is `.env.sample`.

  | Variable | Value | Effect |
  |---|---|---|
  | `DB_BACKEND` | `mongo` | Atlas is the system of record (`memory` is a throwaway dev store) |
  | `MONGO_URL` (legacy alias `DB_URL`) | `mongodb+srv://...` | required with `mongo`; boot fails closed without it |
  | `DB_NAME` | `otoba` | database name |
  | `VOICE_WS_ENABLED` | `true` | `WS /api/v1/chat/v1/{agent_id}` is flag-gated; `false` closes every socket |
  | `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | RS256 PEM pair | login token pairs; both or neither (one alone refuses boot) |
  | `ALLOWED_ORIGINS` | exact `scheme://host[:port]` list | CORS for the UI; a wildcard refuses boot |
  | `REDIS_CACHE_URL` | Upstash `rediss://...`, optional | shared login throttle + JWT denylist; empty = in-process fallbacks |
  | `TWILIO_AUTH_TOKEN` | Twilio token | signature check on `POST /api/v1/voice/inbound/twilio`; empty rejects every inbound call |
  | `COOKIE_SAMESITE=none` + `COOKIE_SECURE=1` | when the UI lives on another site | cross-site session cookie |

  `REDIS_URL` is no longer required (it is only the legacy fallback for
  `REDIS_CACHE_URL`). In a Docker `env_file` write the PEM keys on one line with
  `\n` escapes; the loader decodes them.
- Health: `GET /api/v1/health/live` is dependency-free (the container probe);
  `GET /api/v1/health/ready` reports Atlas and Redis (the Redis probe follows the
  cache client — `REDIS_CACHE_URL`, legacy `REDIS_URL` fallback — and shows
  `skipped` when both are empty).
- Gates before a deploy: `make check` and `make sec`. CI runs the same after
  `make setup` (`.github/workflows/{lint,test,security,modules}.yml`).

## Cutover (one-time)

1. Environment for the app process: `DB_BACKEND=mongo`, `MONGO_URL=<Atlas URI>`,
   `VOICE_WS_ENABLED=true` (the voice websocket is flag-gated), the JWT PEM pair,
   `ALLOWED_ORIGINS`, and `REDIS_CACHE_URL=<Upstash cache URL>` (optional; leave
   empty to run without Redis).
2. Census the legacy Redis before touching anything (no writes; `--redis-url`
   defaults to `REDIS_URL`):

       .venv/bin/python voiceai/tooling/backfill_upstash_to_atlas.py --census --redis-url "$UPSTASH_URL"

3. Copy the durable rows (idempotent; rows already in Atlas are never overwritten;
   `--tenant` stamps every migrated row, default tenant when omitted):

       .venv/bin/python voiceai/tooling/backfill_upstash_to_atlas.py --apply --redis-url "$UPSTASH_URL" --prompts-dir agent_data

   Sessions, refresh rows, throttle counters and denylist entries are counted and
   never copied — users log in once after the switch.
4. Switch traffic to `uvicorn voiceai.app:app --host 0.0.0.0 --port 5001`
   (the Dockerfile CMD). Bare (un-prefixed) paths are gone: every client calls
   `/api/v1/...`.
5. Re-run step 3 once to catch writes that landed during the switch, then stop:
   a later run would resurrect rows deleted in Atlas after the first copy.
6. Verify: `GET /api/v1/health/ready`; the Upstash console command count over
   the next 24h should show only throttle/denylist/readiness traffic.
7. Rollback: quickstart was deleted in Slice D, so rollback is a git revert of
   that slice (Slices A–C are additive) plus the old process env.

## Day two

- Route table: `tests/arch/test_route_inventory.py` pins every `METHOD /api/v1/...`
  pair; changing what the app serves is an edit there, never drift.
- Data lives in `platform_<family>` collections with the legacy payload under
  `payload` and the tenant under `tenant_id`; M6 migrates each family into its
  module and drops these collections.
- The inbound webhook resolves called numbers through `container.inbound_store`
  (tenant-blind read over the same store).
- Telephony: inbound Twilio calls land on `POST /api/v1/voice/inbound/twilio`
  (spec 0047). Realtime media reaches `WS /api/v1/chat/v1/{agent_id}?ticket=…`
  with a single-use ticket from `POST /api/v1/auth/ws-ticket` (60 s TTL). The
  example outbound trunks in `local_setup/telephony_server/` (Twilio/Plivo) mint
  that ticket in their answer callback with `VOICEAI_API_KEY` (an API key with
  the `calls:write` scope) over `VOICEAI_INTERNAL_URL` and dial the ticketed URL
  on the `voiceai-app` ngrok tunnel; a failed mint answers the carrier 500. The
  Talko trunk delegates media to talko-service.
