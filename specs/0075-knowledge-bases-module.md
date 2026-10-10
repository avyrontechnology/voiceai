# Spec 0075: knowledge bases module (retire the `kbs` platform family)

## Goal

Move the `kbs` family out of the legacy `platform` bucket into a real module,
`voiceai/modules/knowledge/`, so the composition root stops serving it through
`platform.store` / `platform.router`.

Second step of the composition-root retirement, and the second family of the
strangler. `numbers` (spec 0074) established the pattern; this one is simpler
still — five endpoints, one collection, and **no cross-module coupling**.

## Why this family next

- `kbs` has no inbound seam like numbers did: nothing in `voiceai/modules/`
  reads the collection. `KnowledgeBaseAgent` (agents module) reads *agent
  config* and talks to a RAG server over HTTP — it never touches these rows. So
  the migration is contained to the family itself.
- Five endpoints, no orchestration, no cross-module reads. The whole risk is
  "did we move five routes and one model faithfully".

## Non-goals

- Migrating `webhooks`/`graphs`/… (each gets its own spec).
- The RAG integration itself. Sources stay references (`pdf`/`url`/`text` + a
  ref string) exactly as today; nothing here fetches, indexes or embeds.
- Removing `platform.store` — still eleven families use it.

## Interface contracts

Module `voiceai/modules/knowledge/`, Rule 1 file set.

### Models (`models.py`)

`KnowledgeBase` becomes a `BaseFields` document — typed fields instead of the
legacy `PlatformRow(payload: dict)` envelope.

- `name: str`
- `sources: list[KBSource]`
- `status: str = "ready"`
- `agent_ids: list[str]` — the agents this KB is attached to.

`KBSource` is a nested value object (`type: pdf|url|text`, `ref: str`), unchanged.

`id` replaces `kb_id` as the document key; the API still returns `kb_id`.

### Service (`service.py`)

- `create(name, sources) -> KnowledgeBase`
- `list_all() -> list[KnowledgeBase]`
- `attach(kb_id, agent_id)` — appends the agent **only if not already present**
  (idempotent, as today)
- `detach(kb_id, agent_id)` — removes every occurrence of the agent
- `delete(kb_id)` — soft delete, raises when absent

### Repository (`repository.py`)

`KnowledgeBaseRepository` over `BaseRepository[KnowledgeBase]`, tenant-scoped.
Reads through `raw_documents()` (added in spec 0074) so pre-migration envelope
rows keep listing — same reasoning as numbers: a KB that stops listing is a KB an
agent can no longer be attached to.

### Errors (`errors.py`)

`KnowledgeBaseNotFoundError(NotFoundError)` → 404, matching the legacy
`_not_found("Knowledge base", kb_id)`.

## Data model

Collection `platform_kbs` is **kept** — the row name predates the module and
renaming would orphan live rows (Rule 5). `Collections.PLATFORM_KBS` stays the
canonical member; the name keeps its `PLATFORM_` prefix for the same reason
`PLATFORM_NUMBERS` does.

Legacy `{payload: {...}}` rows are unwrapped on read by the same
`_as_number`-style fallback the numbers repository uses, and are flattened by a
follow-up backfill.

## Security notes

- Scopes unchanged: `platform:read` on list, `platform:write` on create /
  attach / detach / delete. Migrating the module does not widen access.
- `sources[].ref` can be a URL or inline text. It is **never fetched** by this
  module (fetching is the RAG service's job, behind its own SSRF guard), so the
  module introduces no outbound-call surface. Recorded so a future "let's just
  fetch it here" is a deliberate decision rather than an accident.
- KB names and refs may be customer data: not logged at INFO, only ids.
- Delete is soft (`is_active=False`), consistent with every other erasure here.

## Architecture delta

```
NEW  voiceai/modules/knowledge/{constants,models,errors,repository,service,
                                controller,schemas,static_methods,exceptions,
                                helpers,utils}.py + CONTRACT.md / README.md / RUNBOOK.md
MOD  voiceai/database/constants.py   — PLATFORM_KBS documented as module-owned
MOD  voiceai/core/container.py       — knowledge_service provider + container.wire
MOD  voiceai/modules/__init__.py     — knowledge registered in ALL_MODULES
MOD  voiceai/platform/router.py      — kbs_router removed from single_app_routers
DEL  voiceai/platform/store.py       — save/get/list/delete_kb
DEL  voiceai/platform/models.py      — KnowledgeBase family models
MOD  tooling/backfill_upstash_to_atlas.py — kbs restored typed (as numbers, spec 0074)
```

`test_route_inventory.py` is updated in the same spec (it pins the table and the
platform-route count).

## Test plan

- service: create, list, attach (idempotent — attaching twice adds one entry),
  detach (removes all occurrences), delete, and not-found for each mutation.
- repository: tenant isolation, soft delete, legacy-envelope read.
- controller: the five routes through the real app factory with `httpx`
  (create 201, list 200, attach/detach 200, delete 200 + 404) and the scope gate.
- The legacy behaviour these mirror: attaching an already-attached agent does not
  duplicate it; detaching an unattached agent is a no-op, not a 404.

Coverage ≥85% on the new module.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Same shape as spec 0074: module routers mount before the platform ones and first
match wins, so cutover is a single commit and rollback is reverting it. No
feature flag — the endpoint surface is identical, so a flag would only add a way
to be wrong.

## Burn-down

- [x] `knowledge/constants.py`, `models.py`, `errors.py`
- [x] `knowledge/static_methods.py`, `helpers.py`, `utils.py`
- [x] `knowledge/repository.py`
- [x] `knowledge/service.py`
- [x] `knowledge/schemas.py` + `controller.py` (five routes)
- [x] `knowledge/exceptions.py` + `CONTRACT.md` / `README.md` / `RUNBOOK.md`
- [x] container provider + `container.wire` + module registry
- [x] `platform/router.py` + `platform/store.py` + `platform/models.py`: family removed
- [x] backfill: `restore_knowledge` writes typed rows
- [x] `test_route_inventory.py` (63 platform routes) + the auth shim's `REMOVED_BY_SPEC`
- [x] tests: static methods, service, repository (26 tests)
- [x] `make check` green (2265 passed) + `make sec` clean

## Deviations

- **No inbound seam this time.** Unlike `numbers` (spec 0074), nothing in
  `voiceai/modules/` reads the `kbs` collection — `KnowledgeBaseAgent` reads agent
  config and talks to a RAG server over HTTP. Verified before starting, which is
  why this migration was contained to the family.
- **The service tests run against the real repository**, not a hand-rolled fake.
  A fake pins whatever surface the file happens to touch; the production
  repository over the in-memory driver pins the wiring the app actually uses —
  and it caught that the fake was missing `insert`/`raw_documents`.
- **`test_repository_store.py` moved from `kbs` to `webhooks`.** That file
  exercises bridge isolation/pagination via whichever family is still in the
  platform store, and `kbs` is no longer there. `webhooks` has the same shape,
  so the coverage is unchanged — this is the second time a family has been used
  as the file's fixture, which is worth remembering when the last family goes.
- **Request/response class names changed; the wire shape did not.**
  `CreateKBRequest` → `CreateKnowledgeRequest`, `KBListResponse` →
  `KnowledgeListResponse` (the `knowledgebases` key is unchanged). Recorded in
  `REMOVED_BY_SPEC`.
