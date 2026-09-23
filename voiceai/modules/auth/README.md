# auth

Platform authentication + authorization (strangler tranche C): PBKDF2 passwords,
RS256 JWT access + rotating opaque refresh, single-use WS tickets, invites,
per-IP login throttle, RBAC scopes/roles.

Owned by `squad-identity`. See `CONTRACT.md` and `RUNBOOK.md`. `models/` is a
package (user/principal/session/invite/apikey/audit/revoked) with the public
models re-exported at the package root.
