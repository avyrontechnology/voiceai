# knowledge CONTRACT

Owner: `squad-agents` (`#squad-agents`).

## Routes (mounted under `/api/v1`)

- `POST /knowledgebases` — register a knowledge base, 201 (scope `platform:write`).
- `GET /knowledgebases` — list this tenant's knowledge bases (scope `platform:read`).
- `POST /knowledgebases/{kb_id}/attach` — attach an agent (scope `platform:write`).
- `POST /knowledgebases/{kb_id}/detach` — detach an agent (scope `platform:write`).
- `DELETE /knowledgebases/{kb_id}` — tombstone (scope `platform:write`).

Errors are `KnowledgeBaseNotFoundError` (404).

## Events

- in: none. out: none.

## Collections

- `platform_kbs` (`Collections.PLATFORM_KBS`) — `KnowledgeBase` documents. The row
  name predates this module and is kept so live rows are not orphaned (spec 0075).

## Invariants

- **Attach is idempotent.** Attaching an already-attached agent leaves one entry.
- **Detach is not an error.** Detaching an agent that is not attached returns 200
  with the row unchanged — the end state is what the caller asked for either way.
- **Detach removes every occurrence**, so a row that somehow lists an agent twice
  cannot leave it attached.
- **The module never fetches a source.** `sources[].ref` may be a URL or inline
  text; reading it is the RAG service's job, behind its own SSRF guard. Adding a
  fetch here would introduce an outbound-call surface this module deliberately
  does not have.
- **Delete is soft** (`is_active=False`), consistent with every other erasure.
- **`kb_id` in the response is the document `id`** — the field name is legacy and
  stays for client compatibility.

## Ports

- Consumes `KnowledgeBaseRepository` (tenant-scoped).
- Nothing consumes this module's repository today: `KnowledgeBaseAgent` reads
  *agent config* and talks to a RAG server over HTTP, so the migration carried no
  cross-module seam (unlike `numbers`, spec 0074).

## Layering

`controller → service → repository`. The controller holds no storage call and no
decision; the service owns authorization outcomes and raises the module errors;
the repository is the only layer that touches the driver.

## Out of scope

The RAG integration itself — indexing, embedding and retrieval — is unchanged.
This module owns only the references and which agents they are attached to.
