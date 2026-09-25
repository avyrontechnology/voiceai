# Spec 0026: background-task tenant binding (M4)

## Goal

Background work carries its tenant explicitly: `TaskRegistry.start` accepts
an optional tenant bound for the task's lifetime, and the one new-arch
background spawn (simulated-call progression) passes the caller's tenant
instead of relying on ambient inheritance. Omitted tenants inherit ambient
context as today (engine pumps, call-scoped work) — documented, not changed.

## Non-goals

- Engine pump tasks (they spawn inside bound calls — inheritance is correct).
- Legacy batch/platform background work (cutover-owned, pre-tenancy stores).
- Batch payload `tenant_id` fields (no cross-process jobs exist yet; the
  field arrives with the first one, not before).
- The module-global `_background_tasks` fallback (legacy callers only;
  container wiring already uses the managed registry).

## Interface contracts

- `core/resilience.py::TaskRegistry.start(coro, *, name, tenant=None)`:
  `tenant` (a `TenantContext`) binds around the task body via a wrapper —
  explicit wins over ambient. `None` preserves today's inherit-ambient
  behavior exactly. No other signature change; lifecycle semantics untouched.
- `voice/adapters/outbound.py::start_simulated_call_background(..., tenant)`:
  new optional `tenant` threaded into `registry.start`. `OutboundDialBridge`
  resolves `current_tenant()` eagerly (fail-loud outside a binding —
  background tenant work without a tenant is a wiring bug) and passes it.
  The legacy progression itself is untouched (pre-tenancy store rows).

## Data model

None. No collections, no migrations.

## Security notes

- Explicit tenant cannot escalate: it only selects which rows the already-
  scoped stores serve; the bridge's progression writes legacy rows only.
- `make sec` scope unchanged.

## Test plan

`tests/arch/core/test_resilience.py`: explicit tenant binds for the task
lifetime (task reads `current_tenant()` mid-flight); omitted tenant inherits
ambient; task failures/cancellation semantics unchanged. Bridge test: the
spawned progression carries the caller's tenant (fake registry records it).

## Verification

```sh
make check
make sec
```

## Rollout

Additive optional params; single merge. Rollback is revert.

## Burn-down

- [x] Registry `tenant` param + bridge pass-through + tests.
