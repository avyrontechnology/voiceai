# webhooks

Webhook registrations: which destinations a tenant has, which agent each belongs
to, and what it subscribes to. Migrated out of the legacy `platform` bucket in
spec 0076, where the family was an opaque `{payload: ...}` row behind a
`MemoryStore`.

Read [CONTRACT.md](CONTRACT.md) for the routes, invariants and the security
notes, [RUNBOOK.md](RUNBOOK.md) for operations.

## Layout

| File | Role |
|---|---|
| `constants.py` | Every literal: routes, scopes, defaults |
| `models.py` | `Webhook` — a `BaseFields` document |
| `schemas.py` | Request/response bodies mirroring the legacy API exactly |
| `errors.py` | `WebhookNotFoundError` |
| `exceptions.py` | Guard helpers that raise it |
| `repository.py` | Storage, including the legacy-envelope read |
| `service.py` | Business logic and authorization outcomes |
| `static_methods.py` | Subscription/ownership filtering (pure) |
| `helpers.py` | Small mapping helpers |
| `utils.py` | Process-state utilities |

## Read this before adding a fetch

This module stores destinations. It does **not** call them, and that is not an
oversight — it is why the migration was a routine CRUD move rather than a
security review.

If you ever add delivery here, you are adding an outbound-call surface and must
route every URL through `common.security.is_safe_outbound_url` and give every
call a timeout (AGENTS.md §4). `modules/tools/oauth_service.py:252` is the
in-repo example of doing it right.

Note that the thing which *does* post webhooks today is **not** this module —
it is `WebhookAgent` in the agents module, which currently has neither guard.
See `CONTRACT.md` for that gap.

## Behaviours worth knowing before editing

- **No update endpoint.** The legacy surface had none. Delete and recreate.
- **Empty `events` means every event**, so a new hook is never silently inert.
- **Reads go through `raw_documents()`**: rows written before this migration sit
  under a `{payload: ...}` envelope and would be invisible to a validating
  listing. A hook that stops listing is one an operator configured and can no
  longer see.
