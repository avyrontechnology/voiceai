# Spec 0058: integration endpoints (templates, credentials, links, DCR)

## Goal

Naye vault/integration state (spec 0056 Phase-2: `IntegrationTemplate`,
`PartnerCredential`, `PartnerIntegration`) ko HTTP pe lao taaki UI me
dedicated Integrations section ban sake: provider templates list, credential
connect/revoke (secrets kabhi read-back nahi), agent-link CRUD, aur DCR
register flow — sab tenant-scoped, scope-gated, Mongo-only.

## Non-goals

- OAuth provider/consent/token-minting (S2, traction ke baad); DCR yahan
  sirf *outbound* register (bahar ke MCP servers ke liye client credentials).
- MCP server UI toggle (flag operator-owned, `.env` me).
- `actions:` namespace; campaign/RAG lanes.
- Legacy `/integrations` platform routes ko chhedna nahi (parallel chalenge).

## Interface contracts

Owning module `voiceai/modules/tools` (`squad-platform`), additive only.
New file `integration_service.py` (one responsibility: vault-state
orchestration) + `controller.py` route additions + container wiring. `ToolsService`
untouched.

| Route | Scope | Answers |
| --- | --- | --- |
| `GET /api/v1/integration-templates` | `platform:read` | 200 `{"templates": [...]}` (system + tenant overrides, tenant wins) |
| `GET /api/v1/partner-credentials` | `platform:read` | 200 `{"credentials": [...]}` metadata ONLY (`provider`, `credential_type`, `key_version`, `expires_at` — `encrypted_data` kabhi nahi) |
| `POST /api/v1/partner-credentials` | `platform:write` | 201 metadata + `secret_once` (plaintext sirf is response me, kabhi dobara nahi); body `{provider, credential_type, secret}` — secret vault-encrypted at rest |
| `DELETE /api/v1/partner-credentials/{credential_id}` | `platform:write` | 200 `{"state": "deleted"}` (soft delete = revoke); 404 foreign/missing |
| `GET /api/v1/partner-integrations[?agent_id=]` | `platform:read` | 200 `{"integrations": [...]}` |
| `POST /api/v1/partner-integrations` | `platform:write` | 201 row; body `{provider, agent_id, config}` — provider must have a live credential (400 warna, identifiers only) |
| `DELETE /api/v1/partner-integrations/{integration_id}` | `platform:write` | 200 deleted; 404 foreign/missing |
| `POST /api/v1/integrations/dcr-register` | `platform:write` | 201 `{client_id, issuer_url}` + `client_secret_once` (vault me encrypted save + credential row auto-created); body `{issuer_url}`; undiscoverable/MANUAL issuers → 400 identifiers-only |

- Bodies validate at boundary (pydantic, extras forbidden → shared 422 shape spec 0052).
- Error envelopes shared (`common.responses`); no payload/secret/URL text in error bodies.
- Controller thin (≤30 lines/handler); service holds logic; repository only touches driver.

## Data model

No new collections (0056 names reuse: `integration_templates`,
`partner_credentials`, `partner_integrations` via `VaultRepository`).
`PartnerCredential.encrypted_data` = vault envelope; reads project metadata
only (repository-level projection helper, never `model_dump` of the row).
Deletes soft (`is_active=False`). No migration (collections already declared;
empty until first connect).

## Security notes

- AuthZ service layer; tenant from `current_tenant()`; foreign rows read as 404 (no oracle).
- Secrets: plaintext sirf `POST` response me ek baar (`secret_once` /
  `client_secret_once`); logs me `redact_secrets` + identifiers only;
  `encrypted_data` kabhi wire pe nahi (projection helper + test pin).
- DCR: issuer + registration endpoint SSRF pre-flight (`to_thread`,
  fail-closed); no blind redirects; mandatory timeout.
- `secrets` module for any generated material; `make sec` clean; no new dep
  (`cryptography` already pinned, spec 0056 note).
- Rate-limit/idempotency: connects are idempotent by natural key
  (`provider` per tenant — repeat POST replaces, returns 200+`replaced:true`
  instead of duplicating); unbounded lists paged (`MAX_PAGE_SIZE`).

## Test plan

Unit (service with fake vault/repos/DCR) + repository + controller via real
app factory (`httpx` ASGI), offline only. Matrix: metadata projection (no
`encrypted_data` key anywhere), secret-once (second GET has no secret),
unknown-provider link 400, DCR undiscoverable/MANUAL 400, cross-tenant 404s,
connect-idempotent replace, revoke-then-link 400. Coverage ≥85% touched modules.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Additive routes + service file; flag-independent (scope gates guard).
Rollback = revert. No migration, no shim. UI (`voiceai-ui` MCP section)
consumes these in the same release train (route inventory pin here covers
`PINNED_ROUTES`).

## Burn-down

- [x] `integration_service.py` + vault-metadata projection + DCR flow.
- [x] Controller routes + schemas + container wiring.
- [x] Unit + controller tests (offline).
- [x] `CONTRACT.md` route table + arch-gate deltas (routes, size budget,
  tenant flags) in the same spec.
- [x] Backend gates green (693 arch+tools passed; tools cov 94.61%;
  ruff+bandit clean; mypy clean on touched).
- [x] Backend gates green (695 arch+tools passed; tools cov 95.13%;
  ruff+bandit clean; mypy clean on touched).
- [x] UI: sidebar Tools entry + `/tools` registry CRUD page; integrations
  MCP section (templates create/expand/delete, credentials with expiry
  chips, links, DCR register) — UI green (471 passed, lint 0 errors,
  tsc clean).
- [ ] Commit backend `feat(tools): integration endpoints [spec-0058]` +
  UI `feat(ui): tools + MCP integrations sections`.
