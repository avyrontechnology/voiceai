# Security Review: Submodule Architecture Restructure

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-15 | **Task**: T050

Reviewed against Constitution IX. Method: code inspection + targeted
greps + the automated gates (confinement tests, secret scan, bandit).
No penetration test was run; network/telephone threat model is unchanged
(structural migration only).

## 1. Secrets confinement — PASS

- Zero `os.environ`/`os.getenv` outside `voiceai/core/environment.py`
  (gate-enforced, allowlist empty). All writes go through
  `environment.set_env` (2 call sites, both RAG handoff).
- Secret-pattern scan (AWS keys, GitHub tokens, Slack tokens, live
  secret keys, private-key blocks) clean; CI-enforced in `gate.yml`.
- API keys: full secret returned once at creation, SHA-256 hash stored
  (`services.create_api_key`, tested); listings use `public()` form
  (hash never leaves the server — pre-existing, preserved).
- Stream/carrier secrets read via environment with identical semantics
  (falsy-`""` handling, `_TRUTHY` set, timing all preserved).

## 2. Input validation at the controller boundary — PASS

- All 100 routes keep their Pydantic v2 request models and
  `Query`/`Header` constraints (moved verbatim to `controllers.py`);
  FastAPI auto-422 unchanged. Service-level guards preserved in
  `services.py` (direction 422, unknown-scope 400, definition 422,
  entry-cap 422/409 paths) and covered by service tests.

## 3. Authorization in the service layer — PASS

- Route-level `require_scope`/`require_role`/`require_principal`
  Depends preserved on every route.
- Tenancy enforced inside services: 11 `require_same_org` call sites
  covering every org-bearing resource (executions, batches, tools,
  webhooks); cross-org access raises `AuthorizationError` (tested).
- Admin/owner-only operations (wallet, sub-accounts, org update/reset,
  key mint/delete) keep their role gates; audit trail writes preserved.

## 4. Logs free of secrets and PII — PASS

- No `email`/`phone`/`to_number` in any platform log line (grep-verified).
- Actor emails flow into `AuthEvent` DB records (intended), never logs.
- `redact`/`mask_secret` available for any future log-adjacent output;
  unexpected errors surface only as `Internal error (ref <error_id>)`
  (tested).

## 5. Carrier trust — PASS (unchanged)

- `carrier_auth.py`, `stream_token.py`, telephony providers: only the
  env-reader lines changed (identical semantics); signature checks, token
  verification, and trusted-proxy handling untouched.

## 6. Database least privilege — PASS WITH FOLLOW-UP

- Compose provisions `mongo:8`; credentials flow via `MONGO_URL`/`MONGO_DB`
  (documented in `.env.sample` with a least-privilege provisioning recipe).
- **Follow-up (not blocking: no Mongo traffic exists yet)**: provision the
  `voiceai_app` role and wire `init_beanie` at the persistence cutover
  (US3/Scenario 5 follow-up); until then `MemoryStore`/`RedisStore` remain
  behind the `PlatformRepository` Protocol.

## 7. Residual risks / notes

- `task_manager.py` writes `RAG_SERVER_URL` into the process env for child
  agents (pre-existing design); now via `set_env`, still process-global —
  acceptable, flagged for a future explicit-handoff refactor.
- Bandit repo-wide: 0 medium+ findings (one pre-existing SHA1 cache-key
  hash annotated `usedforsecurity=False`, digest unchanged).
