# Spec 0073: retention policies (GDPR Art. 5(1)(e) storage limitation)

## Goal

Data must not be kept longer than its purpose allows. Add a retention service
that soft-erases expired rows per collection with per-collection TTLs
(env-tunable), driven by the app lifespan loop (no cron dependency).

Retention is **soft**: rows are tombstoned (`is_active=False`), consistent
with the privacy module's erasure contract (spec 0072) — a purge that destroys
audit evidence would break provability.

## Non-goals

- Hard deletion (contradicts auditability; spec-level argument required).
- Consent-record retention: consent rows are the legal record (Arch. 7) and
  are NEVER purged by this service.
- Per-tenant policies — one TTL per collection, from the environment.

## Interface contracts

Owning module `voiceai/modules/privacy`, additive only (retention is a privacy
concern).

- `RetentionService.run_cycle()` — one pass: for every registered collection,
  soft-delete rows whose `updated_at` is older than that collection's TTL.
  Returns a per-collection count.
- Wired as a lifespan loop in `core/app_factory.py`: `asyncio.create_task`
  retained on the container-owned registry (AGENTS.md §5 — every task is
  tracked and cancelled on shutdown), sleeping `RETENTION_INTERVAL_S` between
  cycles, per-iteration exceptions isolated (one bad collection never kills
  the loop).

TTLs come from `core/environment.py`: `retention_privacy_audit_s`,
`retention_chat_sessions_s`, `retention_tool_execution_logs_s` — each defaulting
to a documented value, `0` disabling that collection's purge. There is
deliberately **no** `retention_consent_records_s`: consent rows are the legal
record and are never purgeable, so exposing a TTL for them would be a knob
whose only legal value is "off" (Non-goals).

Retention is **system-wide**, not per-tenant: the purge targets hold *unscoped*
repositories and never read the ambient tenant. A tenant-scoped repository here
would pin the first request's tenant forever and leave every other tenant's
expired rows in place (spec 0020, M1b) — this is what makes the provider a
`Singleton`.

## Data model

N/A — no new collections. Purge targets existing collections via repository
`soft_delete`.

## Security notes

- Consent records are excluded by construction: the retention registry never
  lists `CONSENT_RECORDS`. Pinned by a unit test (a future edit that adds it
  fails the suite).
- Identifier-only logging per cycle.
- No new dep; `make sec` clean.

## Test plan

Unit (service with DI fakes of a purgeable interface) + the loop isolation
(one collection raising mid-cycle does not stop the others). Matrix: TTL expiry
selects old rows, fresh rows untouched, disabled collection skipped, consent
collection absent from the registry, per-iteration error isolation.
Coverage ≥85% touched.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

Retention ships **off** by default: `RETENTION_INTERVAL_S=0`, which stops the
lifespan loop before its first pass. The per-collection TTLs carry documented
values so enabling it later is a matter of setting one interval, not of
re-deciding every policy.

The asymmetry decides the default. Enabling retention later costs one env var;
an unwanted pass silently erases rows. Both erasures are soft (`is_active=False`),
so nothing is destroyed outright — but a row tombstoned by a TTL nobody meant to
enforce is still a wrong answer delivered to a user, and the log is the only
record that it happened. So the safe state is the shipped state, and the rollout
step is: read the TTLs, verify them against what each collection actually holds,
then set the interval.

A deployment that wants retention running immediately sets
`RETENTION_INTERVAL_S=86400` and nothing else. Rollback is reverting the env var;
no code path is involved.

## Burn-down

- [x] `core/environment.py`: three TTL vars + interval (`0` = disabled, the default)
- [x] `privacy/retention.py`: `RetentionService` + collection registry
- [x] `privacy/__init__.py`: export
- [x] `core/container.py`: provider wiring (`Singleton`, unscoped repositories)
- [x] `core/app_factory.py`: lifespan loop + shutdown cancel + the `0` kill switch
- [x] `.env.sample`: documented deployment knobs
- [x] Unit tests (expiry, disabled, consent-exclusion, isolation) + kill-switch pins
- [x] `make check` + `make sec` green; `make cov` above threshold (only the
      pre-existing `test_interruption_gate.py` failure remains, failing on clean HEAD too)

## Deviations

- The interface contract originally listed `retention_consent_records_s`. It was
  removed: consent rows are the legal record (Non-goals), so such a knob would
  only ever legally hold `0`. The absence is pinned by a test instead.
- The container builds **unscoped** repositories rather than
  `_scoped_collection`. Retention is a platform duty spanning all tenants; a
  tenant-scoped repository inside a `Singleton` would pin the first request's
  tenant forever (spec 0020, M1b) and leave every other tenant's expired rows
  untouched.
- The lifespan task is registered without a tenant binding, for the same
  reason. Binding it would misrepresent the scope and, because
  `TaskRegistry._bind_tenant` wraps the coroutine, a shutdown before the first
  step leaves the inner coroutine unawaited (a `RuntimeWarning` observed in
  testing).
- `RETENTION_INTERVAL_S` defaults to `0` (loop off), not `86400`. See Rollout.
  Without the `0` branch the loop would also be a busy spin: `asyncio.sleep(0)`
  yields rather than waits, so an unset-but-positive check would purge on every
  loop iteration instead of once a day.
- The boundary test seeds just *inside* the TTL rather than exactly on it:
  `run_cycle` re-reads the clock, so an exactly-at-TTL row would be expired by
  the microseconds between seeding and the pass. The predicate under test is
  "strictly older than the TTL".

