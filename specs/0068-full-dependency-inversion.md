# Spec 0068: full dependency inversion

## Goal

No service constructs its own dependencies — everything comes from the
container. Today `AuthService` has fallback construction when ports are not
injected (`DefaultTokenService(jwt=jwt, store=store)`, `_StoreUserRepo(store)`).
Remove all fallbacks; the container is the single composition point.

## Non-goals

- No behavior change (existing tests pin behavior)
- No new features
- No interface changes

## Interface contracts

### `voiceai/modules/auth/service.py` — remove fallbacks
- `AuthService.__init__` requires `tokens: TokenService` and `users: UserRepo` — no defaults
- Remove `_StoreUserRepo` backward-compat adapter
- Raise `ValueError` when ports are missing (fail fast)

### `voiceai/core/container.py` — wire all ports
- `auth_service` provider passes `token_service` and `user_repo` providers
- Remove fallback construction from `_build_auth_service`

### Other services — audit
- `ToolsService`, `IntegrationService`, `OAuthService`, `VoiceCallService` — verify all deps come from container
- Remove any remaining fallback construction

## Data model

N/A — no persisted models.

## Security notes

N/A — no auth or secrets.

## Test plan

- Update `test_service.py` — construct `AuthService` with explicit ports (no fallbacks)
- `test_container.py` — verify all providers resolve
- Existing tests pass with updated construction

## Verification

```sh
make check
make sec
```

## Rollback

- Container wiring is the composition point — rollback = revert
- Tests updated in same commit

## Burn-down

- [x] Remove fallback construction from `AuthService` (tokens/users now required)
- [x] `_StoreUserRepo` kept as the test-side adapter (tests construct it explicitly)
- [x] Wire `token_service` + `user_repo` into `auth_service` provider (spec 0067, unchanged)
- [x] Override sites build `DefaultTokenService(jwt=_JWT, ...)` explicitly (offline env has no keys)
- [x] Update tests (27 sites test_service + 20 sites identity/jwt/tenancy/context/controllers)
- [x] `make check` green (2170 passed, bandit clean)
