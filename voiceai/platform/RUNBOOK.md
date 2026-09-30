# Platform surface runbook (spec 0048)

The frozen `platform/router.py` routes are served by the single app (`voiceai.app`)
over `RepositoryPlatformStore`: every row lives in Atlas under `platform_<family>`
collections, auth rows in the auth store. Redis is an optional cache (login
throttle, JWT denylist). This runbook covers the cutover from the retired
quickstart/Redis deployment and the day-two checks.

## Cutover (one-time)

1. Environment for the app process: `DB_BACKEND=mongo`, `MONGO_URL=<Atlas URI>`,
   `VOICE_WS_ENABLED=true` (the voice websocket is flag-gated),
   `REDIS_CACHE_URL=<Upstash cache URL>` (optional; leave empty to run without Redis).
2. Census the legacy Redis before touching anything (no writes):

       .venv/bin/python voiceai/tooling/backfill_upstash_to_atlas.py --census --redis-url "$UPSTASH_URL"

3. Copy the durable rows (idempotent; rows already in Atlas are never overwritten):

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

## Day two

- Route table: `tests/arch/test_route_inventory.py` pins every `METHOD /api/v1/...`
  pair; changing what the app serves is an edit there, never drift.
- Data lives in `platform_<family>` collections with the legacy payload under
  `payload` and the tenant under `tenant_id`; M6 migrates each family into its
  module and drops these collections.
- The inbound webhook resolves called numbers through `container.inbound_store`
  (tenant-blind read over the same store).
