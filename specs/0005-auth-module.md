# Spec 0005 — Auth module (platform auth strangler, tranche C)

- **Status:** in progress
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** spec 0001 (foundation); spec 0002 (ports-and-adapters precedent, agents `__all__` discipline); AGENTS.md §3.1

## Goal

Carve the **authentication + authorization domain** into `voiceai/modules/auth`: the
credential primitives (PBKDF2 hashing, token minting, Principal + scope/role gates),
the auth models (users, sessions, invites, API keys, audit events + request/response
schemas), an `AuthService` over a store port, and the `/auth/*` routes as a thin
controller. The legacy platform suite keeps passing after every step via same-named
delegators and identity shims; the deployed quickstart keeps serving unchanged.

## Non-goals

- The rest of `platform/` (agent records live in agents since 0002; graphs, batches,
  wallets, integrations, simulation, workflows, talko-dialer stay legacy — later specs).
- A new store implementation: `MemoryStore`/`RedisStore` stay the backends; the module
  depends on an `AuthStorePort` they satisfy structurally.
- Container-DI rewiring of the platform app: the controller keeps reading the store
  from `app.state` (the deployed quickstart owns that key) until the platform-app
  cutover endgame; per-request service construction through `Depends` needs no
  container changes.
- Password-policy changes, MFA, OAuth/SSO, or touching the PBKDF2 parameters.
- Merging `revamp/resilient-core` (its own spec).

## Design

Hexagonal strangler, the spec-0002 shape: `modules/auth` owns the domain and an
`AuthStorePort` the legacy stores satisfy WITHOUT importing `voiceai.modules.*`;
bridge code (none needed — structural conformance) and legacy re-export shims follow
AGENTS.md §3.1; the engine-visible error surface translates `AppError` subclasses to
the exact legacy HTTP status codes in the controller, so status-asserting suites
cannot tell the difference.

### Target tree (estimated lines; nothing over 1,500, target ≤ 800)

```
voiceai/modules/auth/
  __init__.py [60]      MODULE (ModuleDef) + __all__ — the ONLY import path others may use
  constants.py [60]     SESSION_COOKIE ("otoba_session"), SESSION_TTL_S, REMEMBER_TTL_S,
                        WS_TICKET_TTL_S, INVITE_TTL_S, LOGIN_WINDOW_S, LOGIN_MAX_ATTEMPTS,
                        cookie flag names
  ports.py [80]         AuthStorePort (runtime_checkable Protocol): exactly the ~20 store
                        methods auth touches (users/sessions/invites/keys/events CRUD +
                        list_user_sessions, new for the password-change path)
  models/__init__.py [40]   explicit re-export of the auth schema surface
  models/user.py [120]      (moved: models.py UserRole/User, ROLE_RANK/ROLE_SCOPES/
                        ALL_SCOPES/UserRole literals. HTTP envelopes — SignupRequest,
                        LoginRequest, SetRoleRequest, ChangePasswordRequest, UserResponse,
                        UserListResponse — live in controller.py instead (the agents A4
                        precedent: envelopes belong to the boundary, not the schema).
                        `utcnow`/`new_id` are NOT duplicated here: `utcnow` is spelled
                        `common.datetime_utils.utc_now` (semantically identical, verified),
                        and `new_id` moves to `common/ids.py` (new, generic per rule 2,
                        with tests) while `platform/models.py` re-exports it by alias
                        import (3-line legacy edit, zero behavior change, single source))
  models/session.py [60]    (moved: SessionRecord only — AuthMeResponse/WsTicketResponse
                        are output envelopes, controller.py)
  models/invite.py [60]     (moved: Invite only — InviteListResponse/CreateInviteResponse/
                        AcceptInviteRequest/InviteRequest are envelopes, controller.py)
  models/apikey.py [60]     (moved: ApiKey only — list/create envelopes stay with their
                        (legacy) routes; CreateApiKeyRequest unused by auth routes)
  models/audit.py [40]      (moved: AuthEvent only — AuthEventListResponse is an envelope,
                        controller.py)
  models/principal.py [80]  (moved: Principal dataclass + has_scope/has_role/effective_scopes)
  errors.py [60]        AuthError(AppError), InvalidCredentialsError(401),
                        ForbiddenError(403), TooManyAttemptsError(429), InviteInvalidError(400),
                        NotFoundError reuse for missing user/invite (404)
  exceptions.py [40]    ensure_* guard helpers
  static_methods.py [80] (moved: auth.py hash_password/verify_password/new_token/token_hash)
  utils.py [60]         the per-process login limiter (`_attempts` + `check_login_allowed`;
                        module-global state = impure by rule 1g, so not static_methods)
  helpers.py [40]       public_user projection (moved; the ledger shape clients pin)
  adapters/cookies.py [20]  (§3.1 bridge) COOKIE_SECURE/COOKIE_SAMESITE read exactly as
                        legacy does today (module-level os.getenv); retires when env owns
                        them endgame — keeps os.getenv out of service/controller (rule 4)
  service.py [300]      AuthService over AuthStorePort: signup/login/logout/invite/accept/
                        users/role/delete/password/ticket/audit (+ throttle check + audit
                        writes, incl. the best-effort swallow). No HTTP types (rule 1e);
                        raises module errors, never HTTPException. check_login_allowed's
                        429 surfaces as TooManyAttemptsError.
  controller.py [280]   /auth/* on the new app factory at /api/v1 (shapes byte-identical
                        incl. the last-owner 400s, self-role/self-delete 400s, invite 409s);
                        defines its own `get_store` (moved 6-line app.state seam — the
                        legacy copy stays for the other routers) + `client_ip` helper +
                        a SINGLE `_to_http(exc)` mapper every handler funnels through
                        (the C6 map test enumerates it); per-request
                        AuthService(store=Depends(get_store)). Mounted by adding
                        `auth.MODULE` to `ALL_MODULES` (the voice-B0 1↔1 registry-test
                        update); legacy `auth_router.py` stays mounted by quickstart.
```

**Stays legacy this phase:** `platform/auth.py` (same-named delegators + re-exports →
shim at cutover — full surface: Principal, audit, require_principal/role/scope,
token_hash, get_store, redeem_ws_ticket, hash/verify_password, mint/revoke_session,
mint_ws_ticket, new_token, public_user, check_login_allowed, client_ip,
get_principal, _principal_from_session/_principal_from_api_key (quickstart binds the
former), all TTL/count/cookie constants), `platform/auth_router.py` (mounted by
quickstart; the new controller is proven by its own suite, cutover flips the mount
in endgame), `platform/models.py` (superset shim over the moved schema + 2 alias
imports for `new_id`/`utcnow`), `platform/store.py` (+1 additive method:
`list_user_sessions` on both stores for the password path — additive only, no
restructure), `local_setup/quickstart_server.py` (deployed entry; routes/shapes
frozen), `voiceai/enums.py`, `local_setup/telephony_server/*`.

## Behavior-invariant checklist (normative; regression test lands in C1 for any entry lacking one)

PBKDF2 `$pbkdf2_sha256$600000$<salt>$<digest>` format incl. verify-against-legacy-hash
· malformed-reference verify → False (never raise) · constant-time compare on both
comparisons · `secrets` (never `random`) for tokens · first-user-becomes-owner then
signup-closes-403 · last-owner guard (400 on role-change AND delete) · self-role /
self-delete 400s · duplicate-email 409s · invite expiry/tz-naive handling · API-key
prefix+expiry+liveness update · ws-ticket single-use 60s · login throttle 5/min/IP ·
audit best-effort swallow · password-change kills other sessions only · transcripts/
emails at INFO only (PII rule §4: the signup log line carries the email — preserved
quirk with TODO, owned by the platform strangler, never re-fixed here).

## Migration steps (universal gate as defined in spec 0002; one commit per step)

- **C0 — Auth scaffold + ports (pure addition).** `__init__` (MODULE + `__all__`),
  `constants`, `ports.AuthStorePort`, `errors`, `exceptions`; module-def + port
  conformance tests (MemoryStore/RedisStore satisfy the port structurally).
- **C1 — Pure-function moves + safety net (tests + delegators only).**
  `static_methods` (hash/verify/token ×4, delegators stay); characterization tests
  for every checklist entry lacking one (PBKDF2 vectors incl. cross-version verify,
  throttle math, Principal gate truth tables on concrete values).
- **C2 — Models split.** `models/*` moved (delegators stay); `platform/models.py`
  superset shim; `dir()` snapshot + engine-free canary (the A2 precedent: models
  import zero engine code).
- **C3 — Service + ports (DONE, commit `feat(auth)` [spec-0005]).** `AuthService`
  over `AuthStorePort` (+`list_user_sessions` on both stores); `utils` limiter,
  `helpers.public_user`, `adapters/cookies.py` bridge; 34-test service suite with DI
  fakes (98% pkg cov). Absorbed C4 whole (line-by-line moves are inseparable —
  limiter/audit/swallow/sweep all ride the service bodies).
  - DEVIATION 1 (security fix, test-pinned): the API-key resolver gains the
    `disabled` gate the session resolver always had — verbatim legacy lets a
    disabled user's keys keep working. Legacy keeps the hole till cutover.
  - DEVIATION 2 (C2 IOU, mechanical): port payloads re-pointed to the moved
    models (`Any` → `User`/`SessionRecord`/`Invite`/`ApiKey`/`AuthEvent`).
  - Notes: signup-dup 409 unreachable (closed-gate first, verbatim order, pinned);
    set_role/delete last-owner guards unreachable via API (self-check first,
    pinned directly); R3 retired by the port method; R5 done (caveat in `utils`).
- **C4 — FOLDED INTO C3 (no-op).** Its planned scope (limiter, audit, sweep) moved
  with the service bodies; nothing remains for a separate commit.
- **C5 — Controller + cutover-ready (DONE, commit `feat(auth)` [spec-0005]).**
  `controller.py` thirteen routes on the factory at `/api/v1/auth` (ASGI suite,
  16 tests, 99% pkg cov); envelopes live in the controller; mounted via
  `ALL_MODULES` (+registry-test 1↔1); legacy `auth_router.py` untouched.
  - Byte identity holds at the PAYLOAD level (rule 2 overrules the spec's literal
    "byte-identical": identical statuses, identical `detail` strings, identical
    `data` payloads inside the standard envelopes; endgame cutover owns clients).
  - `get_store` maps the 503 seam onto `DependencyUnavailableError` (same string);
    `authenticate` raises "Authentication required" (login keeps "Invalid email or
    password"); `service.me()` added for /me (controller holds no store access).
  - Cookie fix vs legacy shape: set/clear on the RETURNED response (the injected
    one is discarded when a handler returns JSONResponse).
  - Normalization note: the STORES lowercase inside `get_user_by_email`, so login
    is case-insensitive in both worlds; invite keeps the saner normalized dup
    check (legacy raw lookup could double-register `User@X` over `user@x`).
- **C6 — Closeout + shim audit.** Burn-down reconciled; an arch test asserts the
  controller's error→status map covers every service error; line-count proof; final
  `make check` + `test-all` + `sec` + `cov`.

## Security notes

- **Boundary validation:** every body/query already a pydantic model (moves verbatim);
  new service layer never trusts client identity — acting user always from auth context.
- **Error opacity:** service raises `AppError`; only the controller maps to HTTP status
  with the legacy `detail` strings (status-asserting suites pin them); `str(exc)` in a
  response stays a violation.
- **Secrets:** hashes only at rest (PBKDF2/SHA-256, `secrets` module); `db_url`-style
  handling N/A (no new secret env vars — cookie flags already declared); `redact_secrets`
  before logging any mapping; the signup INFO email line is a preserved PII quirk + TODO.
- **Crypto:** PBKDF2-SHA256/600k + `hmac.compare_digest` preserved verbatim (both
  comparisons); parameters frozen by non-goal above.
- **Abuse:** login throttle preserved verbatim (per-process 5/min/IP documented limit,
  multi-worker caveat rides along); password change keeps the kill-others semantics.
- **Injection:** no string-built queries (store dicts only); no `eval`; file paths N/A.
- **Supply chain:** no new dependency (stdlib + fastapi + pydantic, all already pinned).

## Test plan

C1 is the plan's heart: characterization before movement (PBKDF2 vectors, throttle,
Principal gates). Per-step gates above; port conformance suites; the A2 `dir()`
snapshot + engine-free canary for models; controller suite through the real app
factory (httpx ASGI transport); coverage ≥ 85% on all new packages.

## Verification

Universal gate per step (spec 0002 definition); baselines re-snapshotted per step; every
rewrite carries a reconciliation table.

### Tranche C baseline (to be measured and recorded by C0)

- `.venv/bin/python -m pytest -q --collect-only` count + `make test-all` failing set.

## Risks (register)

- **R1 legacy-shim drift:** same-named delegators per moved name, deleted only with
  ported tests (the 0002/0004 iron rule); burn-down reconciled at C6.
- **R2 status-code drift:** suite asserts HTTP statuses, so the AppError→status map
  gets its own C6 arch test (every service error covered).
- **R3 store-privates reach:** `change_password` reads `store._redis`/`store._data`
  today; the port gains `list_user_sessions` and both stores implement it (additive).
- **R4 quickstart freeze:** the deployed entry is never edited; the new controller
  proves itself on the new factory until the endgame cutover spec.
- **R5 throttle placement:** per-process limiter moves behind the service seam
  verbatim (documented multi-worker caveat included); a shared limiter is endgame.

## Rollout

Strangler: every step green and revertible; quickstart remains the deployed entry until
the flagged controller cutover (endgame spec). Endgame specs after C6: mount flip
(quickstart → new factory for /auth), container store wiring (app.state → container),
shared login limiter, platform strangler continuation (graphs/batches/wallets).
