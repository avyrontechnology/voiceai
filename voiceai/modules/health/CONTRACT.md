# health CONTRACT

Owner: `squad-platform` (`#squad-platform`).

## Routes (mounted under `/api/v1/health`)

- `GET /api/v1/health` — full dependency report (`HealthReport`: app/redis/database).
- `GET /api/v1/health/live` — liveness, no dependency probes (always 200 when up).
- `GET /api/v1/health/ready` — readiness, 200 when ready, 503 with
  `unavailable_components` detail otherwise.

## Events

- in: none. out: none.

## Collections

- `health_checks` — single well-known probe row (`id="health-probe"`), overwritten
  per call so polling cannot grow the store.
