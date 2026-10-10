# Knowledge — Runbook

Knowledge bases and their agent attachments. Migrated out of the legacy
`platform` bucket in spec 0075.

## Endpoints

| Method | Path | Scope | What it does |
|---|---|---|---|
| POST | `/api/v1/knowledgebases` | `platform:write` | Register a knowledge base (201) |
| GET | `/api/v1/knowledgebases` | `platform:read` | List this tenant's knowledge bases |
| POST | `/api/v1/knowledgebases/{id}/attach` | `platform:write` | Attach an agent (idempotent) |
| POST | `/api/v1/knowledgebases/{id}/detach` | `platform:write` | Detach an agent |
| DELETE | `/api/v1/knowledgebases/{id}` | `platform:write` | Tombstone |

## How an agent uses a knowledge base

This module stores **references**, not content. An agent configured with a
knowledge base talks to the RAG service over HTTP; `sources[].ref` is read there,
behind that service's own SSRF guard. Nothing in this module fetches a URL.

So a knowledge base that "isn't loading" is almost never an HTTP problem here —
check the row, then the agent's config, then the RAG service.

## Attach semantics

`attach` is **idempotent**: attaching the same agent twice leaves one entry. This
is the legacy behaviour and clients depend on it — a retried request must not
produce a duplicated entry.

`detach` is **never an error** for an unattached agent; it returns 200 with the
row unchanged. The caller asked for the agent to not be attached, and it is not.

## Operational notes

- Delete is a **soft** delete (`is_active=False`). The knowledge base stops
  listing and stops being attachable immediately, but the row remains for audit.
- Knowledge base names and source refs may be customer data. Only ids are logged.
- Rows written before spec 0075 sit under a `{payload: ...}` envelope and are
  unwrapped on read, so an old knowledge base stays attachable. A flattening
  backfill removes the shape; the fallback goes with it.

## Troubleshooting

| Symptom | Check |
|---|---|
| 404 on attach/detach | The knowledge base was deleted (tombstoned) or belongs to another tenant. |
| Agent attached but sees nothing | The agent's config does not reference this knowledge base id, or the RAG service has no indexed content for these sources. |
| Attach seemed to do nothing | It is idempotent — check whether the agent was already attached. |
