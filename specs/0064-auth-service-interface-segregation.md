# Spec 0064: AuthService interface segregation (ISP)

## Goal

`AuthService` (4 files, 1251 total lines) owns too many responsibilities: session
storage, JWT minting, user CRUD, tenancy resolution, ticket minting. Split into
focused ports (interfaces) so each concern is independently testable and
replaceable.

## Non-goals

- No behavior change (existing tests pin behavior)
- No new features
- No database migration
- Legacy session bridge unchanged

## Interface contracts

All under `voiceai/modules/auth/`. New package structure:

### `ports.py` — interfaces (new)
- `SessionStore` (Protocol): `get_session`, `save_session`, `delete_session`, `get_user`
- `TokenService` (Protocol): `mint_jwt_pair`, `verify_jwt`, `refresh_jwt`
- `UserRepo` (Protocol): `get_by_email`, `get_by_id`, `create`, `update_version`

### `stores/mongo_session_store.py` (new)
- `MongoSessionStore` — implements `SessionStore` over `MongoDB`
- Session CRUD with soft delete

### `services/token_service.py` (new)
- `TokenService` — JWT mint/verify/refresh
- Uses `JwtSettings` from `service_base.py`

### `repos/mongo_user_repo.py` (new)
- `MongoUserRepo` — implements `UserRepo`
- User CRUD + version bump

### `service.py` (rewrite, ~150 lines)
- `AuthService` — orchestrator over ports
- Receives `SessionStore`, `TokenService`, `UserRepo` via container
- Methods: `signup`, `login`, `refresh`, `logout`, `authenticate`, `resolve_caller`, `me`, `redeem_ticket`, `mint_ticket`

### Unchanged
- `service_base.py` — `JwtSettings`, `SessionTokens`, `AuthServiceBase` (session creation, tenancy, audit)
- `service_identity.py` — `AuthIdentityMixin`
- `service_admin.py` — `AuthAdminMixin`

## Data model

N/A — no new collections. Existing `sessions`, `users` unchanged.

## Security notes

- JWT signing key from env (unchanged)
- Session tokens hashed before storage (unchanged)
- Rate limiting on login (unchanged)

## Test plan

- Existing `test_service.py`, `test_identity.py` pass unchanged
- New: `test_ports.py` — protocol conformance tests
- New: `test_mongo_session_store.py` — store with fake DB
- New: `test_token_service.py` — JWT mint/verify/refresh

## Verification

```sh
make check
make sec
```

## Rollback

- Old `service.py` kept as `service_legacy.py` until cutover
- Container wires new `AuthService` with ports
- Rollback = revert

## Burn-down

- [ ] `ports.py` — SessionStore, TokenService, UserRepo interfaces
- [ ] `stores/mongo_session_store.py` — SessionStore implementation
- [ ] `services/token_service.py` — JWT mint/verify/refresh
- [ ] `repos/mongo_user_repo.py` — UserRepo implementation
- [ ] Rewrite `service.py` — orchestrator over ports
- [ ] Container wiring — inject ports
- [ ] Update tests
- [ ] `make check` green
