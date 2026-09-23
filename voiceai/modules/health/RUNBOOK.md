# health RUNBOOK

Owner: `squad-platform` (`#squad-platform`).

## Alerts

- `readiness 503` — check `unavailable_components` in the response: `redis` means
  the cache URL is wrong or Redis is down; `database` means the round-trip
  insert+get of the probe row failed. App stays up; traffic degrades, not dies.
- `liveness failing` — the process itself is wedged; restart the deployment.

## Scaling

Stateless; safe to replicate. Probes are cheap (one Redis ping + one probe-row
round-trip). Poll `/live` every few seconds, `/ready` for load-balancer gates,
full report for diagnostics only.

## Rollback

No migrations. Revert the owning spec's merge; the probe row is idempotent.
