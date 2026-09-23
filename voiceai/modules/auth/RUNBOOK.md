# auth RUNBOOK

Owner: `squad-identity` (`#squad-identity`).

## Alerts

- Login spike + `TooManyAttemptsError` — throttle working as designed; check for
  credential-stuffing source IPs before widening limits.
- `401 loop after deploy` — classic `SameSite=None` without `Secure` (fails closed
  at `Environment` validation) or JWT keypair half-configured (both keys or
  neither). Check env, not code.
- Session invalid everywhere — `token_version` bump revokes all user sessions by
  design (last-owner guard prevents lockout).

## Scaling

Stateless service; Redis denylist/throttle is the only shared state and fails
open with error logs. Clock skew breaks JWT `exp` — keep NTP healthy.

## Rollback

No migrations. Token formats are versioned (`ver` claim); old sessions survive a
revert unless a version bump shipped in the same change.
