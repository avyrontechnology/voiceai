# Spec 0059: custom integrations (all auth types + creation-time schema + auto-sync)

## Goal

Poora custom integration builder: provider banao, **har auth type** (`none`,
`api_key` header/query, `bearer`, `basic`, `oauth2`, `custom` headers) configure
karo, **creation time pe tool schemas define karo** (name/desc/method/path +
JSON-Schema parameters), aur **ek click pe saare tools registry me auto-add**
karo — realtime, redeploy-free. Executor har type ko headers/query me resolve
kare; vault kabhi plaintext wapas nahi deta.

## Non-goals

- OAuth2 token *refresh* flow (401 once-retry rehta hai; refresh S2).
- `actions:` namespace; MCP server UI toggle; campaign/RAG.
- Legacy `/integrations` platform routes ko chhedna nahi.
- Query-param secrets ka SSRF-exempt hona — pre-flight pehle jaisa hi hai.

## Interface contracts

Owning module `voiceai/modules/tools` (`squad-platform`), additive only.

- Auth model: `CredentialType = none|api_key|bearer|basic|oauth2|custom`
  (models + constants + UI enum, ek hi vocabulary). `PartnerCredential`
  gains `auth_config: dict` (NON-secret: api_key `{location, name}`,
  basic `{username}`, custom `{}` — values sealed). Sealed JSON material per
  type: api_key `{"api_key"}`, bearer/oauth2 `{"token"}`, basic `{"password"}`,
  custom `{"headers": {...}}`. `auth_ref` format pins to `cred:<id>`
  (unknown formats fail closed — never bare).
- `ToolAuthResolver.resolve_auth(tool, *, force_refresh) -> AuthParams(headers,
  query)`: type-switched header/query builder (pure `_auth_params_for`
  + store fetch); `resolve_headers` stays as headers-only compat. Cache stores
  both halves (TTL same, fail-open same).
- Executor `_execute_http`/`_call_with_auth_retry` use `resolve_auth`; query
  half merges into request params (GET: with arguments; others: URL query +
  JSON body). Header-only creds behave byte-identical to today.
- `VaultCredentialStore` (new `credential_repository.py`, implements
  `CredentialStorePort`): `cred:<id>` → tenant vault row → decrypt → envelope
  `{credential_type, auth_config, **material}`; unknown/corrupt → `None`
  (resolver fails closed, identifiers only). Container wires it into
  `ToolExecutor` (replacing `credential_store=None` — auth tools start
  working).
- `IntegrationService.sync_tools(provider) -> {created, updated, skipped,
  tool_ids}`: template entries `{name, description?, parameters?, path?,
  method?}` → `function:{name}` rows (`url = client_config.base_url + path`,
  `auth_ref = cred:<live-id>` or `None` when type `none`; non-`none` without
  a live credential → 400 first). Re-sync updates ONLY rows stamped
  `meta.synced_from == provider` (hand-made same-name rows are `skipped`,
  never clobbered). Entries validated (name slug, method allowlist,
  parameters must be an object) — problems collect, valid ones still sync.
- `POST /integration-templates/{provider}/sync-tools` (`platform:write`) →
  201 `{created, updated, skipped, tool_ids}`; 400 no-credential/unsafe-base
  (base_url SSRF pre-flight, fail-closed); 404 unknown provider.
- UI builder (same release train, voiceai-ui): 4-step modal — provider,
  auth (dynamic per-type fields + secret-once), tools editor (name/desc/
  method/path + parameters JSON textarea, client-validated, add/remove),
  review → save template → connect → sync → created/updated counts.

## Data model

- `PartnerCredential.auth_config: dict` (new, non-secret; default `{}`).
  No migration (collections schemaless; readers `.get` with defaults).
- `ToolDefinition.meta["synced_from"] = provider` marks auto rows;
  `auth_ref = "cred:<id>"` (new format; old arbitrary refs fail closed
  through the store as before).
- Deletes stay soft. Template `client_config["base_url"]` is the only new
  honored key (validated absolute http(s) at sync).

## Security notes

- Secrets: sealed JSON only (api_key/token/password/headers-values);
  `auth_config` me kabhi secret nahi (service validates: username/names
  only — a `password`/`token`/`secret` key inside `auth_config` is 400).
- Query-param keys: values never logged (redact + identifier-only lines);
  note: URLs land in access logs upstream — UI warns on query mode.
- SSRF: template `base_url` + per-tool URL pre-flight at sync AND at call
  (existing executor guard, unchanged); no blind redirects; timeouts.
- Error opacity: envelopes + `error_id`; `str(exc)` never on wire.
- AuthZ: service layer; tenant-scoped; foreign → 404 no-oracle.
- `make sec` clean; no new dep.

## Test plan

Unit (auth matrix: 6 types + unknown-format + corrupt-envelope + cache
both-halves; sync: create/update/skip-clobber/no-credential/bad-entry/
unsafe-base) + controller (sync happy/400s/404, envelope shape) + executor
(query-merge test) + UI (builder validation, hook bodies, secret-once),
offline only (MockTransport, fake DNS/store). Coverage ≥85% touched.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Additive (new file + fields + 1 route); existing header-only behavior
byte-identical. Old arbitrary `auth_ref` values keep failing closed exactly
as today. Rollback = revert. UI builder consumes new endpoints same train.

## Burn-down

- [x] Auth model (`CredentialType` ×6, `auth_config`, `cred:` refs).
- [x] `credential_repository.py` + container wiring (store stops being `None`).
- [x] `resolve_auth` + executor query merge.
- [x] `sync_tools` + route + validation.
- [x] UI builder wizard + hooks + tests.
- [x] CONTRACT + arch-gate deltas in the same spec.
- [x] Gates green (backend 2151 passed, cov 94.6%, ruff/bandit/mypy
  clean; UI 475 passed, lint 0 errors, tsc clean).
- [ ] Commit `feat(tools): custom integrations [spec-0059]` + UI commit.
