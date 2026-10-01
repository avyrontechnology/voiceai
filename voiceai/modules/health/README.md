# health

Probes this service's dependencies and exposes them over HTTP. The canonical
minimal module: constants/models/schemas/errors/exceptions/ports-free
repository/service/controller plus helpers/utils/static_methods.

The Redis probe pings the container's cache client (`REDIS_CACHE_URL`, legacy
`REDIS_URL` fallback) — the Redis the app actually uses (spec 0053).

Owned by `squad-platform`. See `CONTRACT.md` for routes and `RUNBOOK.md` for ops.
