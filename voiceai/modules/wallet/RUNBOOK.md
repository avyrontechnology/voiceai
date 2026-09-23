# wallet RUNBOOK

Owner: `squad-billing` (`#squad-billing`).

## Alerts

- Top-up succeeds but balance unchanged — check ledger for a duplicate submit
  (no idempotency key on this route yet; retry storms double-post — see spec
  backlog) before blaming the store.
- Template import 404s after seed deploy — seeder has not upserted
  `agent_templates` yet; re-run the seeder, then read the row directly.

## Scaling

Stateless service over three `BaseRepository`s. Ledger lists page at 100 and
filter in memory (documented in repository) — fine at current volumes; move the
filter into the query when ledger pages grow a tail.

## Rollback

Ledger is append-only — never delete rows to "undo" a top-up; post a reversing
`debit` entry instead. Template rows are seed-owned; a revert re-seeds.
