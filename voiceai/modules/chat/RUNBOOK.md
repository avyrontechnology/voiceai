# chat RUNBOOK

Owner: `squad-platform` (#squad-platform).

## Alerts

- 404 spike on POST: agent resolution failing (definitions store or tenant).
- 400 spike: non-chat agents addressed (builder misconfiguration).

## Scaling

Stateless service; history bounded at 100 messages per session.

## Rollback

Revert the owning spec's merge; no migrations in normal operation.
