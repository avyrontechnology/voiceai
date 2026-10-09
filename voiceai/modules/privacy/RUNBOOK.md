# privacy RUNBOOK

Owner: `squad-platform` (`#squad-platform`).

## Alerts

- `consent 404 spike` — callers withdrawing unknown subject/purpose pairs; check
  whether a client release changed subject-id hashing (raw emails must never be
  sent — the service stores the opaque id verbatim).
- `consent_records growth` — grants are append-mostly; withdrawals tombstone
  rather than delete. Retention purges land with spec 0073; until then, watch
  collection size on the Atlas dashboard.

## Scaling

Stateless; safe to replicate. All reads are single-subject `find_many` windows;
no collection scans. Redis is not touched by this module.
