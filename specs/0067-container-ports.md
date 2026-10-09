# Spec 0067: container ports (DIP)

## Goal

Define `SessionStore`, `TokenService`, and `UserRepo` as `typing.Protocol` in
`core/ports.py` so services depend on abstractions, not concrete implementations
(Dependency Inversion Principle). The container wires concrete implementations;
services never construct their own dependencies.

## Non-goals

- No behavior change (existing wiring unchanged)
- No new features
- No interface changes to existing protocols

## Interface contracts

New file: `voiceai/core/ports.py`

### Protocols (re-export from module ports)
- `SessionStore` — re-export from `voiceai.modules.auth.ports.AuthStorePort`
- `TokenService` — re-export from `voiceai.modules.auth.ports.TokenService`
- `UserRepo` — re-export from `voiceai.modules.auth.repos.mongo_user_repo.MongoUserRepo` (structural)

### Container changes
- `voiceai/core/container.py` — type annotations updated:
  - `auth_service` provider: `AuthService` receives `SessionStore`, `TokenService`, `UserRepo`
  - `token_service` provider: `DefaultTokenService` (Singleton)
  - `user_repo` provider: `MongoUserRepo` (Factory, per-request)

## Data model

N/A — no persisted models.

## Security notes

N/A — no auth or secrets.

## Test plan

- `test_container_ports.py` — verify container resolves ports correctly
- Existing container tests pass unchanged

## Verification

```sh
make check
make sec
```

## Rollback

- Ports are additive — no existing code modified
- Rollback = revert

## Burn-down

- [ ] Create `core/ports.py` — re-export protocols
- [ ] Update `container.py` — type annotations + wiring
- [ ] `test_container_ports.py` — port resolution tests
- [ ] `make check` green
