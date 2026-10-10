# Spec 0077: integrations module (retire the `integrations` platform family)

## Goal

Move the `integrations` family out of the legacy `platform` bucket into a real
module, `voiceai/modules/integrations/`.

Fourth family of the composition-root strangler. Five endpoints, one collection
— but, unlike the three before it, this family holds **credentials**, so the
migration carries a security contract rather than a CRUD shape.

## Why this family is different

`Integration.config` is an open dict of provider credentials (Twilio/Plivo/Talko
API keys, Calcom tokens, and so on). The legacy surface protects it by masking
every secret-looking value on the way out. That masking **is** the security
contract here, and a migration that dropped it would leak every stored credential
through the API. So the masking moves with the family, and gets pinned by tests.

## A bug this migration must not carry over

The legacy read path masks secrets; the write path does not un-mask them:

```python
# GET  →  {..., "config": {"api_key": "••••••••"}}
# PUT  →  {..., "config": {"api_key": "••••••••"}}     # client echoes what it read
# store → {**stored.config, **payload.config}          # → "••••••••" overwrites the real key
```

A client that reads an integration and PUTs it back — the ordinary round-trip —
**destroys the stored credential**, replacing it with eight bullet characters.
There is no way to recover it short of re-entering the secret by hand.

The fix is to make the write path ignore a masked value wherever it would
overwrite a stored one: a payload key whose value is exactly the mask marker is
treated as "unchanged" and skipped. This is behaviour-preserving for every
client that was not doing the destructive round-trip, and it makes the
destructive one stop destroying. It is called out in CONTRACT.md and RUNBOOK.md
rather than applied silently, because it is a change in what a PUT means.

## Non-goals

- Migrating `graphs`/`workflows`/`runs`/`campaigns` (an automation cluster, own spec).
- Adding an update endpoint that does not exist — `PUT` is the only update path,
  as today.
- Any change to which config keys count as secret. `_SECRET_HINTS` moves verbatim;
  widening it is a separate decision with its own review.

## Interface contracts

Module `voiceai/modules/integrations/`, Rule 1 file set.

### Models (`models.py`)

`Integration` becomes a `BaseFields` document, replacing the legacy
`PlatformRow(payload: dict)` envelope.

- `kind` — closed vocabulary of eleven providers (`twilio|plivo|exotel|vobiz|
  talko|calcom|n8n|zapier|sheets|sip|truecaller`), carried over verbatim.
- `name: str`
- `config: dict[str, Any]` — the credential bag. **Never returned raw.**
- `enabled: bool = True`

`id` replaces `integration_id`; the API still returns `integration_id`.

### Static methods (`static_methods.py`)

- `mask_secrets(config) -> dict` — moved verbatim from
  `platform/models.py:442`: any **string** value whose field name contains
  `key`/`secret`/`token`/`password` (case-insensitive) becomes `••••••••`.
  Non-string values are passed through untouched, so a nested dict or a numeric
  token is not mangled — same as today.
- `MASKED_SECRET` — the marker constant.
- `merge_config(stored, incoming) -> dict` — the update merge **plus** the
  masked-value guard described above.

### Service (`service.py`)

- `create(kind, name, config, enabled) -> Integration`
- `list_all() -> list[Integration]`
- `get(integration_id) -> Integration`
- `update(integration_id, name?, config?, enabled?) -> Integration` — partial
  update; omitted fields are untouched, and `config` merges per key rather than
  replacing wholesale (the legacy `{**old, **new}`).
- `delete(integration_id)` — soft delete, raises when absent.

Every read returns the **masked** view; only the store ever holds raw values.

### Repository (`repository.py`)

`IntegrationRepository` over `BaseRepository[Integration]`, tenant-scoped. Reads
through `raw_documents()` so pre-migration envelope rows keep listing — a
credential an operator cannot see is a credential they will re-create.

### Errors (`errors.py`)

`IntegrationNotFoundError(NotFoundError)` → 404, matching the legacy
`_not_found("Integration", integration_id)`.

## Data model

Collection `platform_integrations` is **kept** — the row name predates the module
and renaming would orphan live credentials (Rule 5).

Legacy `{payload: {...}}` rows are unwrapped on read; a follow-up backfill
flattens them.

## Security notes

- **Masking is the contract.** Every read path (create, list, get, update)
  returns masked config. A test asserts a raw secret never appears in a
  response body, and that the *store* still holds it.
- **The masked-write guard** is the fix above. Tested by round-tripping a GET
  response back through the update path and asserting the stored secret survives.
- **Never log `config`.** Only ids and the provider `kind`. A config dump is a
  credential dump (AGENTS.md §4).
- Scopes unchanged: `platform:read` on list/get, `platform:write` on
  create/update/delete.
- Delete is soft (`is_active=False`) — deleting an integration does not erase the
  credential row, which is also the audit trail.
- `_SECRET_HINTS` moves verbatim. It is a heuristic, and a key that does not look
  secret (`"sid"` say) is still returned raw — that is today's behaviour, and
  narrowing it silently would break clients that read their own config back.

## Architecture delta

```
NEW  voiceai/modules/integrations/{constants,models,errors,repository,service,
                                   controller,schemas,static_methods,exceptions,
                                   helpers,utils}.py + CONTRACT.md / README.md / RUNBOOK.md
MOD  voiceai/database/constants.py   — PLATFORM_INTEGRATIONS documented as module-owned
MOD  voiceai/core/container.py       — integrations_service provider + container.wire
MOD  voiceai/modules/__init__.py     — integrations registered in ALL_MODULES
MOD  voiceai/platform/router.py      — integrations_router removed
DEL  voiceai/platform/store.py       — save/get/list/delete_integration
DEL  voiceai/platform/models.py      — Integration family models + mask_secrets
MOD  tooling/backfill_upstash_to_atlas.py — _MODULE_RESTORES entry
MOD  tests/arch/platform/test_repository_store.py — fixture moves off this family
```

`test_route_inventory.py` is updated in the same spec.

## Test plan

- static methods: masking hits on `key`/`secret`/`token`/`password`, in any
  case; a non-string value under a secret-looking name is passed through; a
  nested dict is passed through.
- **the round-trip guard**: GET a masked config, PUT it back, assert the stored
  raw secret is unchanged and a genuinely new key is still merged.
- service: create/list/get/update/delete; partial update leaves omitted fields
  alone; `config` merges per key; update bumps `updated_at`.
- **every read returns masked** and the store keeps raw — the core security pin.
- repository: tenant isolation, soft delete, legacy-envelope read.
- controller: the five routes through the real app factory with `httpx`
  (create 201, list 200, get 200 + 404, put 200, delete 200 + 404) and the scope
  gate.

Coverage ≥85% on the new module.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Same shape as specs 0074–0076: module routers mount before the platform ones and
first match wins, so cutover is one commit and rollback is reverting it.

**Deploy note:** this is the first family in the strangler whose cutover changes
behaviour rather than only shape — the masked-write guard. It makes a
destructive client round-trip stop destroying credentials. Anyone relying on
"PUT the masked value to blank the field" would now see the field unchanged;
that is a deliberate trade, since the alternative is silently losing real
credentials.

## Burn-down

- [x] `integrations/constants.py`, `models.py`, `errors.py`
- [x] `integrations/static_methods.py` (masking + merge), `helpers.py`, `utils.py`
- [x] `integrations/repository.py`, `service.py`
- [x] `integrations/schemas.py` + `controller.py` (five routes)
- [x] `integrations/exceptions.py` + `CONTRACT.md` / `README.md` / `RUNBOOK.md`
- [x] container provider + `container.wire` + module registry
- [x] `platform/{router,store,models}.py`: family removed (platform routers 13 → 12)
- [x] backfill: `_MODULE_RESTORES` entry
- [x] `test_route_inventory.py` (55 platform routes) + auth shim `REMOVED_BY_SPEC`
      (6 names — the 4 models plus `mask_secrets` and `MASKED_SECRET`)
- [x] `test_repository_store.py` fixture moved off this family
- [x] tests: masking, the merge guard, the end-to-end round-trip, service,
      repository, tenant isolation (20 tests)
- [x] `make check` green (2298 passed) + `make sec` clean

## Deviations

- **The masked-write guard is a behaviour change, and the spec is the record of
  it.** Sending `••••••••` in a `PUT` used to overwrite the stored credential with
  the mask; it now means "unchanged". The one capability this removes is
  "PUT the mask to blank a field" — clients that want that must omit the field.
  Chosen because not-clearing is recoverable and a destroyed credential is not.
  Documented in `CONTRACT.md` and `RUNBOOK.md`, not applied silently.
- **Masking is applied in the service, not the controller.** `list_all` would
  otherwise leak every stored credential in one response, so the service returns
  masked copies and the controller physically cannot hold a raw credential. The
  schema layer maps what it is given and does not re-mask, so masking cannot be
  forgotten at a call site nor double-applied.
- **`MASKED_SECRET` carries a `# noqa: S105`.** The name trips the hardcoded
  password heuristic; it is a mask marker, not a secret.
- **`test_repository_store.py` moved from `integrations` to `tools`.** Fourth
  family to serve as that file's fixture. `tools` is the next family still in the
  platform store with a full CRUD shape — `batches` and `executions` were checked
  first and lack a delete.
- **`test_tenant_isolation.py` flagged list.** The tenant-isolation pin was added
  rather than assumed: this is the first family where a cross-tenant read would
  leak a *credential* rather than a name.
