# Spec 0076: webhooks module (retire the `webhooks` platform family)

## Goal

Move the `webhooks` family out of the legacy `platform` bucket into a real
module, `voiceai/modules/webhooks/`.

Third family of the composition-root strangler. Three endpoints, one
collection, no outbound calls from the module itself.

## Non-goals

- **Firing webhooks.** This family is a CRUD registry only; nothing in the
  codebase reads it to make an outbound call (verified before starting). The
  thing that *does* fire — `WebhookAgent` — takes its URL from **agent config**
  via `voice/modules/voice/session/composition.py`, not from this collection.
  Migrating the family therefore moves no outbound-call surface.
- Migrating `graphs`/`workflows`/`…` (each gets its own spec).
- Removing `platform.store` — still eleven families use it.

## Interface contracts

Module `voiceai/modules/webhooks/`, Rule 1 file set.

### Models (`models.py`)

`Webhook` becomes a `BaseFields` document, replacing the legacy
`PlatformRow(payload: dict)` envelope.

- `agent_id: str | None` — the agent this hook belongs to; `None` = tenant-wide.
- `url: str` — the destination.
- `events: list[str]` — the event names this hook subscribes to.
- `enabled: bool = True`

`id` replaces `webhook_id`; the API still returns `webhook_id`.

### Service (`service.py`)

- `create(agent_id, url, events, enabled) -> Webhook`
- `list_all() -> list[Webhook]`
- `delete(webhook_id)` — soft delete, raises when absent

No update/patch endpoint exists in the legacy surface, so none is invented.

### Repository (`repository.py`)

`WebhookRepository` over `BaseRepository[Webhook]`, tenant-scoped. Reads through
`raw_documents()` so pre-migration envelope rows keep listing — same reasoning as
specs 0074/0075: a hook that stops listing is one an operator thinks they
configured and cannot see.

### Errors (`errors.py`)

`WebhookNotFoundError(NotFoundError)` → 404, matching the legacy
`_not_found("Webhook", webhook_id)`.

## Data model

Collection `platform_webhooks` is **kept** — the row name predates the module and
renaming would orphan live rows (Rule 5). `Collections.PLATFORM_WEBHOOKS` stays
the canonical member, prefix and all.

Legacy `{payload: {...}}` rows are unwrapped on read; a follow-up backfill
flattens them.

## Security notes

- **Scoping does not widen.** `platform:read` on list, `platform:write` on
  create/delete — exactly what the legacy router required.
- **`url` is stored, never fetched by this module.** That is the whole reason
  this migration is not a security change. A future edit that adds a fetch here
  would introduce an outbound surface without the SSRF guard; the guard is
  `common.security.is_safe_outbound_url` and, if that day comes, this module
  needs the same treatment `modules/tools/oauth_service.py:252` already gets.
- **A live gap is recorded, not silently carried over.** The thing that actually
  fires webhooks today is `WebhookAgent`
  (`modules/agents/brains/webhook.py:49`), which posts to its URL with **no
  SSRF guard and no timeout** — flagged there as `# TODO(spec-0004): ... legacy
  parity`. Its URL comes from agent config, so it is out of this spec's scope,
  but the gap is real and is called out here rather than left implicit. It wants
  its own spec; see Rollout.
- URLs are operator-supplied infrastructure addresses: logged by id, never the
  full URL (a URL can carry a token in its query string).
- Delete is soft (`is_active=False`), consistent with every other erasure here.

## Architecture delta

```
NEW  voiceai/modules/webhooks/{constants,models,errors,repository,service,
                               controller,schemas,static_methods,exceptions,
                               helpers,utils}.py + CONTRACT.md / README.md / RUNBOOK.md
MOD  voiceai/database/constants.py   — PLATFORM_WEBHOOKS documented as module-owned
MOD  voiceai/core/container.py       — webhooks_service provider + container.wire
MOD  voiceai/modules/__init__.py     — webhooks registered in ALL_MODULES
MOD  voiceai/platform/router.py      — webhooks_router removed
DEL  voiceai/platform/store.py       — save/get/list/delete_webhook
DEL  voiceai/platform/models.py      — Webhook family models
MOD  tooling/backfill_upstash_to_atlas.py — webhooks restored typed
MOD  tests/arch/platform/test_repository_store.py — its fixture moves off this
     family (see Deviations)
```

`test_route_inventory.py` is updated in the same spec.

## Test plan

- service: create, list, delete, not-found for delete; a tenant-wide hook
  (`agent_id=None`) round-trips; `enabled=False` round-trips.
- repository: tenant isolation, soft delete, legacy-envelope read.
- controller: the three routes through the real app factory with `httpx`
  (create 201, list 200, delete 200 + 404) and the scope gate.
- The legacy surface had no update endpoint — a test asserts the module mounts
  exactly three routes, so an invented PATCH cannot slip in unnoticed.

Coverage ≥85% on the new module.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Same shape as specs 0074/0075: module routers mount before the platform ones and
first match wins, so cutover is one commit and rollback is reverting it.

Nothing fires these rows, so the blast radius of the cutover is the three CRUD
endpoints and nothing else.

**Not in this spec:** the `WebhookAgent` SSRF/timeout gap. It is a separate
concern with a separate owner (the agents module) and deserves its own spec
rather than riding along on a migration.

## Burn-down

- [x] `webhooks/constants.py`, `models.py`, `errors.py`
- [x] `webhooks/static_methods.py`, `helpers.py`, `utils.py`
- [x] `webhooks/repository.py`, `service.py`
- [x] `webhooks/schemas.py` + `controller.py` (three routes)
- [x] `webhooks/exceptions.py` + `CONTRACT.md` / `README.md` / `RUNBOOK.md`
- [x] container provider + `container.wire` + module registry
- [x] `platform/{router,store,models}.py`: family removed (platform routers 14 → 13)
- [x] backfill: `restore_webhook` writes typed rows
- [x] `test_route_inventory.py` (60 platform routes) + auth shim `REMOVED_BY_SPEC`
- [x] `test_repository_store.py` fixture moved off this family
- [x] tests: pure helpers, service, repository (13 tests)
- [x] `make check` green (2278 passed) + `make sec` clean

## Deviations

- **A real security gap was found and deliberately NOT closed here.**
  `WebhookAgent` (`modules/agents/brains/webhook.py:49`) posts to its URL with
  no SSRF guard and no timeout, carrying an inline `# TODO(spec-0004)`. Its URL
  comes from agent config, not from this collection, so migrating the family
  neither causes nor fixes it. It is recorded in `CONTRACT.md` and `RUNBOOK.md`
  so the migration is not mistaken for having closed it, and it wants its own
  spec against the agents module.
- **The backfill's family dispatch was rewritten as a table.** The third
  module-owned family made the nested-ternary chain in `_apply_platform`
  genuinely unreadable, so `_MODULE_RESTORES` maps family → restore method and
  the call site is flat. A family added to `MODULE_FAMILIES` without a table
  entry would silently fall back to the platform envelope its new module cannot
  read, so the two are declared side by side.
- **A first cut at the legacy router deletion removed `inbound` as well.** The
  slice ran to the next `@`-decorated route rather than to the next section
  header, and `inbound` is the family immediately after `webhooks`. Caught by an
  immediate import check (`NameError: name 'inbound_router' is not defined`),
  reverted, and re-cut on the section boundary with an assertion that the block
  contains no foreign router.
- **`test_repository_store.py` moved from `webhooks` to `integrations`.** That
  file runs the bridge's isolation/pagination coverage against whichever family
  is still in the platform store; `integrations` still is, and has the same CRUD
  shape. This is the third family to serve as that fixture.
