# knowledge

Knowledge bases: the named source references an agent can be attached to, and
which agents are attached to which. Migrated out of the legacy `platform` bucket
in spec 0075, where the family was an opaque `{payload: ...}` row behind a
`MemoryStore`.

Read [CONTRACT.md](CONTRACT.md) for the routes and invariants,
[RUNBOOK.md](RUNBOOK.md) for operations.

## Layout

| File | Role |
|---|---|
| `constants.py` | Every literal: routes, scopes, defaults, source vocabulary |
| `models.py` | `KnowledgeBase`, `KBSource` — `BaseFields` documents |
| `schemas.py` | Request/response bodies mirroring the legacy API exactly |
| `errors.py` | `KnowledgeBaseNotFoundError` |
| `exceptions.py` | Guard helpers that raise it |
| `repository.py` | Storage, including the legacy-envelope read |
| `service.py` | Business logic and authorization outcomes |
| `static_methods.py` | Attach/detach list work (pure) |
| `helpers.py` | Small mapping helpers |
| `utils.py` | Process-state utilities |

## What this module does not do

It stores references, not content. `sources[].ref` is never fetched here — the
RAG service reads it behind its own SSRF guard. `KnowledgeBaseAgent` reads agent
config and talks to that service over HTTP; it does not read these rows.

## Behaviours worth knowing before editing

- **Attach is idempotent** and **detach of an unattached agent is a no-op**. Both
  mirror the legacy endpoints exactly; a "cleanup" that turns either into a 409 or
  a 404 changes the API contract. Pinned in
  `test_knowledge_static_methods.py`.
- **Detach filters rather than popping one occurrence**, so a duplicated row
  cannot leave an agent attached.
- **Reads go through `raw_documents()`**, not the typed listing: rows written
  before this migration sit under a `{payload: ...}` envelope and would be
  invisible to a validating listing. A knowledge base that stops listing is one
  an agent can no longer be attached to.
