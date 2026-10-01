# health CONTRACT

Owner: `squad-platform` (`#squad-platform`).

## Routes (mounted under `/api/v1/health`)

- `GET /api/v1/health` — full dependency report (`HealthReport`: app/redis/database).
- `GET /api/v1/health/live` — liveness, no dependency probes (always 200 when up).
- `GET /api/v1/health/ready` — readiness, 200 when ready, 503 with
  `unavailable_components` detail otherwise.

## Probed dependencies

- `redis` — one `PING` on the container's **cache client** (`redis_cache`:
  `REDIS_CACHE_URL`, legacy `REDIS_URL` fallback), the same client the JWT denylist
  uses (spec 0053). `skipped` only when no cache URL is effective (both empty);
  `down` when the ping fails or times out. The legacy single-URL `redis_client` is
  never probed.
- `database` — insert + get of the probe row on the deployment's backend.
- `/live` probes nothing.

## Events

- in: none. out: none.

## Collections

- `health_checks` — single well-known probe row (`id="health-probe"`), overwritten
  per call so polling cannot grow the store.
