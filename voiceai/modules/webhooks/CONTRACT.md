# webhooks CONTRACT

Owner: `squad-platform` (`#squad-platform`).

## Routes (mounted under `/api/v1`)

- `POST /webhooks` — register a destination, 201 (scope `platform:write`).
- `GET /webhooks` — list this tenant's webhooks (scope `platform:read`).
- `DELETE /webhooks/{webhook_id}` — tombstone (scope `platform:write`).

There is **no update/PATCH endpoint.** The legacy router had none, and inventing
one would be an unrequested API addition. To change a hook, delete and recreate.

Errors are `WebhookNotFoundError` (404).

## Events

- in: none. out: none.

## Collections

- `platform_webhooks` (`Collections.PLATFORM_WEBHOOKS`) — `Webhook` documents.
  The row name predates this module and is kept so live rows are not orphaned
  (spec 0076).

## Invariants

- **This module never delivers anything.** It records destinations; it does not
  call them. Nothing in the codebase reads this collection to make an outbound
  request (verified before the migration).
- **Delete is soft** (`is_active=False`), consistent with every other erasure.
- **`webhook_id` in the response is the document `id`** — the field name is
  legacy and stays for client compatibility.
- **An empty `events` list means "every event"**, not "nothing". A freshly
  created hook with no subscriptions would otherwise be silently inert.
- **A hook with no `agent_id` is tenant-wide** and applies to every agent.

## Security

- `url` is stored, never fetched by this module. A future edit that adds a fetch
  would introduce an outbound surface *without* the SSRF guard this module
  deliberately does not have. If that day comes, it needs the same treatment
  `modules/tools/oauth_service.py:252` already gets
  (`common.security.is_safe_outbound_url`).
- URLs are logged by id, never in full — a destination can carry a token in its
  query string (AGENTS.md §4).

### A real gap this module does NOT close

The thing that actually posts webhooks today is **`WebhookAgent`**
(`voiceai/modules/agents/brains/webhook.py:49`), not this module. It calls
`session.post(webhook_url)` with **no SSRF guard and no timeout**, carrying an
inline `# TODO(spec-0004): ... legacy parity`.

Its URL comes from **agent config** (wired in
`voiceai/modules/voice/session/composition.py:724`), not from this collection —
which is precisely why migrating this family moves no outbound surface and why
the gap is *not* fixed by this spec. It needs its own spec with its own owner
(the agents module). Recorded here so the migration is not mistaken for having
closed it.

## Layering

`controller → service → repository`. The controller holds no storage call and no
decision; the service owns authorization outcomes and raises the module errors;
the repository is the only layer that touches the driver.

## Out of scope

Webhook **delivery**. This module is a registry; whoever delivers does so against
its own rules, not this module's.
