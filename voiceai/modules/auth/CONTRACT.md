# auth CONTRACT

Owner: `squad-identity` (`#squad-identity`).

## Routes (mounted under `/api/v1/auth`)

Signup/login/refresh/logout/me, invites (`invite/invites/accept`), users
(list/set-role/delete), password change, WS tickets, auth events. `__all__` is
the public surface; other modules resolve callers via `AuthService.authenticate`
+ `ensure_permitted` and never trust client-supplied identity fields.

## Events

- in: none. out: none (audit rows, not bus events).

## Collections

- `users` (id = `user_id`), `sessions` (id = token hash; kinds
  session/ws-ticket/refresh), `invites`, `api_keys`, `auth_events`,
  `revoked_tokens`. Revocation truth is the store with a Redis read-through
  (`auth:denied:{jti}`); login throttling is Redis counters (`auth:throttle:{ip}`,
  5/min, fail-open with error log).
