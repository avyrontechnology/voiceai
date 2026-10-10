# Webhooks — Runbook

Webhook registrations for the tenant. Migrated out of the legacy `platform`
bucket in spec 0076.

## Endpoints

| Method | Path | Scope | What it does |
|---|---|---|---|
| POST | `/api/v1/webhooks` | `platform:write` | Register a destination (201) |
| GET | `/api/v1/webhooks` | `platform:read` | List this tenant's webhooks |
| DELETE | `/api/v1/webhooks/{id}` | `platform:write` | Tombstone |

**There is no update endpoint.** Change a hook by deleting and recreating it —
that is the legacy contract, kept deliberately.

## What this module does and does not do

It **stores** destinations. It does not deliver to them. Nothing in the codebase
reads this collection to make an outbound request.

So if a hook is registered and nothing arrives, that is **not** this module's
fault — the delivery path lives elsewhere. See "The delivery gap" below.

## Fields

| Field | Meaning |
|---|---|
| `agent_id` | Owning agent. **Omitted means tenant-wide** — applies to every agent. |
| `url` | The destination. Stored, never called here. |
| `events` | Event names to subscribe to. **Empty means every event**, not none. |
| `enabled` | Whether the hook is active. |

## The delivery gap (read before debugging a "webhook not firing")

The component that actually posts webhooks is **`WebhookAgent`**
(`voiceai/modules/agents/brains/webhook.py`), and it takes its URL from **agent
config**, not from these rows:

- wired in `voiceai/modules/voice/session/composition.py:724`
- it calls `session.post(webhook_url)` with **no SSRF guard and no timeout**
  (marked `# TODO(spec-0004)` for legacy parity)

This gap is real and this module does not close it. If you need delivery fixed,
it wants its own spec against the agents module — not a change here.

## Troubleshooting

| Symptom | Check |
|---|---|
| Hook registered but nothing fires | Expected if delivery is the `WebhookAgent` path — check the agent's config, not this collection. |
| Hook missing from the list | It was deleted (soft — the row survives, `is_active=False`), or it belongs to another tenant. |
| Hook ignores events | `events: []` means *all* events, not none. An unexpected non-empty list is what filters. |
| 404 on delete | Already deleted, or another tenant's hook. |

## Security notes

- URLs are logged by **id, never in full** — a destination can carry a token in
  its query string.
- Registering a hook needs `platform:write`; listing needs `platform:read`.
- Do not add delivery to this module without the SSRF guard
  (`common.security.is_safe_outbound_url`) and a mandatory timeout.
