# Spec 0061: full OAuth2 connector (login, callback, refresh)

## Goal

OAuth2 ko *complete* karo: aaj token-paste + DCR-register + Bearer-execute
hai; missing hai **user-login flow** — provider login page → callback →
code exchange (PKCE) → sealed tokens → lazy refresh. Iske baad "Connect with
{provider}" ek click pe kaam karega, token paste kiye bina.

## Non-goals

- OAuth provider *hosting* (hum client hai, DCR se register karte hai —
  server-side 0057 pilot alag hai).
- Device flow, JWT-bearer grants, mTLS.
- Auto-refresh inside the executor hot path (401 once-retry rehta hai;
  refresh explicit endpoint + UI button; auto-lazy S2).

## Interface contracts

Owning module `voiceai/modules/tools` (`squad-platform`), additive only.
New file `oauth_service.py` + controller routes + `PUBLIC_BASE_URL` env.

- `POST /oauth/authorize-url` (`platform:write`): body
  `{provider, authorization_endpoint, token_endpoint, client_id,
  client_secret?, scope?}` → 200 `{authorization_url, expires_in}`.
  Server: `state` (32B hex) + PKCE `verifier`/`challenge(S256)`; stores
  `{provider, verifier, client_*, token_endpoint, tenant_id}` in Redis
  (`oauth:state:{state}`, TTL 600, single-use). Endpoints SSRF-checked
  (fail-closed); `PUBLIC_BASE_URL` empty → 400 dark (callback unaddressable).
  `redirect_uri = {PUBLIC_BASE_URL}/api/v1/oauth/callback` (pinned).
- `GET /oauth/callback?code=&state=` (public browser route): state lookup
  (missing/expired → 400 page, identifiers only) → single-use delete →
  tenant pinned from the state record (never a query param) → code exchange
  (`grant_type=authorization_code`, Basic auth when a secret exists, else
  body `client_id`) → seal `{token, refresh_token?, client_secret?}` as an
  oauth2 credential (idempotent by provider) → HTML page that
  `postMessage({type:"oauth:connected", provider})` + closes (no secrets in
  the page). Exchange/token-endpoint failures → 400 page (no token text).
- `POST /partner-credentials/{id}/refresh` (`platform:write`): needs a
  stored `refresh_token` + `token_endpoint` + `client_id` in
  (`auth_config`/`material`) else 400; `grant_type=refresh_token` → reseal
  (preserve secret when unrotated) → 200 metadata. Foreign/missing → 404.
- `auth_config` gains `{client_id, token_endpoint}` (non-secret, needed for
  refresh); sealed material gains `refresh_token?` + `client_secret?`
  (resolver ignores extra keys — bearer shape unchanged).
- UI builder oauth2 step: third option "Login" (endpoints + client id/secret
  fields) → popup to `authorization_url` → `message` listener → success
  marks connected (no secret shown); publish skips manual connect then.

## Data model

No new collections. Credential rows: `auth_config{client_id?,
token_endpoint?}`, sealed JSON `{token, refresh_token?, client_secret?}`.
Redis: `oauth:state:{state}` TTL 600 (requires Redis — `None` cache →
400 "OAuth login needs Redis", documented). Deletes stay soft.

## Security notes

- `state` single-use + TTL + tenant pinned server-side (CSRF/replay safe);
  `tenant_id` never from query/body — always the state record.
- PKCE S256 always (public + confidential clients).
- Secrets (`client_secret`, tokens) sealed immediately; Redis state holds
  the client secret ≤10 min single-use (documented; same posture as
  session cookies in Redis).
- SSRF on both endpoints; no blind redirects; timeouts mandatory.
- Callback page carries no secrets (provider + status only); error pages
  identifiers-only. `make sec` clean; no new dep.

## Test plan

Unit (service with fake redis/store/vault + MockTransport token endpoint:
authorize URL shape + state stored; callback happy seals + deletes state;
bad/expired state 400; exchange failure 400 + stores nothing; refresh happy
reseals + preserves secret; refresh without refresh_token 400) +
controller (authorize 400s, callback pages, refresh 404) + UI (login tab,
popup/message flow with mocked hooks), offline only. Coverage ≥85% touched.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Additive (1 service file + 3 routes + 1 env var). `PUBLIC_BASE_URL` empty
keeps login dark (paste/DCR still work). Rollback = revert. UI login tab
consumes same train.

## Burn-down

- [x] `oauth_service.py` (authorize/callback/refresh) + schemas + routes.
- [x] `PUBLIC_BASE_URL` env + container wiring.
- [x] Unit + controller tests (offline).
- [x] UI login flow + tests.
- [x] CONTRACT + arch-gate deltas in the same spec.
- [x] Gates green (backend 2165 passed, cov 94.09%, ruff/bandit/mypy
  clean; UI 476 passed, lint 0 errors, tsc clean).
- [x] Commit `feat(tools): full OAuth2 connector (authorize, callback, PKCE refresh) [spec-0061]` (backend landed; UI login tab remains for the UI train).
