# Spec 0040: identity program — Tenant PK + org/teams (Phase D)

## Goal

Replace the bare `org_id` string with real identity entities: `Tenant`
(ObjectId PK), `Organization`, `Team`, `Membership` (per-team roles).
Module rows reference the tenant object hex; auth carries tenant + teams;
existing single-org data migrates to `Tenant("default")`.

## Non-goals

- Per-team billing/quotas; SSO/SCIM; team-scoped API keys.
- UI implementation (spec 0041 covers it).
- Deleting `org_id` fields (deprecated in place; removal is T7).
- `BaseFields` changes (ids stay `str` — hex fits).

## Decisions (approved)

1. Tenant PK = ObjectId hex; module `tenant_id` fields carry it (one id
   form everywhere; supersedes M1b verbatim-org, documented here).
2. Extend `auth` (no new module; the identity shim keeps re-exporting).
3. Migrate org default → Tenant rows (no indefinite dual-write).
4. Per-team roles (`Membership.role`); global `User.role` stays fallback.

## Interface contracts (binding — no agent improvises field names)

**Models (`auth/models/`, owner: Agent A).** All inherit `BaseFields`;
natural keys pinned as `id` by the repository (auth `_pin` precedent):

- `Tenant`: `tenant_id: str` (natural key = ObjectId hex, generated via
  `bson.ObjectId()` at creation; test fakes may pass explicit hex),
  `slug: str` (unique human key, e.g. `"default"`), `name: str`,
  `plan: str = "default"`, `status: Literal["active","suspended"] =
  "active"`. Collection `tenants` (new `Collections` member — integrator).
- `Organization`: `org_id: str` (natural key `org_<hex12>` via
  `common.ids.new_id`), `tenant_id: str` (object hex), `name: str`.
  Collection `organizations` (integrator).
- `Team`: `team_id: str` (natural key `team_<hex12>`), `org_id: str`,
  `tenant_id: str` (object hex, denormalized for scoping), `name: str`.
  Collection `teams` (integrator).
- `Membership`: `membership_id: str` (natural key `mem_<hex12>`),
  `user_id: str`, `team_id: str`, `org_id: str`, `tenant_id: str`
  (object hex), `role: UserRole`. Collection `memberships` (integrator).
  Uniqueness on `(user_id, team_id)` enforced in service (duplicate →
  `ConflictError`), not by index (memory backend has none).

**Repository (owner: Agent A).** Extend `MongoAuthStore` (or the port FIRST
— `AuthStorePort` in `auth/ports.py` gains: `save_tenant`,
`get_tenant`, `get_tenant_by_slug`, `list_tenants`, `save_organization`,
`get_organization`, `list_organizations(tenant_id)`, `save_team`,
`get_team`, `list_teams(org_id)`, `save_membership`,
`list_memberships(user_id)`, `delete_membership`; fake in
`auth/tests/test_service.py::FakeAuthStore` extended by Agent A in that
file ONLY — shared test file, Agent A owns these additions, Agent B must
not edit it).

**Service CRUD (owner: Agent A).** `AuthService` gains: `create_tenant`
(slug unique → `ConflictError`), `create_organization`,
`create_team`, `add_membership` (duplicate → `ConflictError`),
`remove_membership`, `list_user_teams(user_id)`; all take acting
`Principal` + owner/admin gates via existing `_require_owner` /
`ensure_permitted` patterns. NO resolver changes (Agent B).

**Controller CRUD (owner: Agent A).** `POST /auth/tenants`,
`POST /auth/organizations`, `POST /auth/teams`,
`POST /auth/teams/{id}/members`, `DELETE
/auth/teams/{team_id}/members/{user_id}`, `GET /auth/me/teams`
(owner/admin gates via existing controller patterns; thin handlers).

**Auth runtime (owner: Agent B).**
- `Principal` gains `tenant_id: str = "system"` (object hex post-migration;
  default keeps anonymous/system paths total) + `teams: list[str] = []`
  (team ids) + `team_roles: dict[str,str] = {}` (team id → role).
- All three resolvers (`_principal_from_session/jwt/api_key`) populate
  `tenant_id` from the user's resolved tenant + `teams`/`team_roles` from
  memberships. Unknown user → existing behavior (None/401 paths untouched).
- `User`: add `tenant_id: str | None = None` (object hex, stamped at
  creation from the acting tenant; default `None` = pre-identity row);
  RETIRE the `_sync_tenant_from_org` validator (delete it — explicit
  stamping replaces it). Same for `SessionRecord`, `Invite` (retire both
  validators). `org_id` fields STAY (deprecated, still populated).
- `me()`: verify user tenant matches principal tenant (existing org-check
  pattern, now on hex).
- Middleware (`core/app_factory.py` — INTEGRATOR-owned, Agent B must NOT
  touch): binds `principal.org_id` today; integrator switches it to
  `principal.tenant_id` with slug fallback.
- Tests: Agent B owns `auth/tests/test_tenant_context.py` (new file) +
  updates to `test_service.py` fixtures for new model fields ONLY where
  constructors break (coordinate: Agent A extends FakeAuthStore; Agent B
  must not reformat it).

**Migration (owner: Agent C).**
- `tooling/backfill_tenant_id.py` gains: `ensure_default_tenant(store)`
  (create `Tenant("default")` iff missing slug; return its hex) +
  `rewrite_tenant_refs` rule: every `tenant_id`/`org_id` value equal to a
  legacy org slug rewrites to that org's tenant hex; `"system"` and
  already-hex values pass through. Pure helpers + table updates, tested
  offline (`tests/arch/test_backfill_tenant_id.py` — Agent C owns
  additions there; integrator owns nothing in that file... wait, shared
  file collision: Agent C owns the WHOLE test additions; integrator stays
  out. Fine.).
- Runbook section in this spec file? NO — runbook lives in spec body
  below (integrator writes it at integration from Agent C's report).
- `tooling/validate_agents.py` untouched.

**Integrator-owned shared files (agents must NOT touch):**
`voiceai/database/constants.py`, `voiceai/database/base.py`,
`voiceai/core/container.py`, `voiceai/core/app_factory.py`,
`voiceai/modules/__init__.py`, `tests/arch/test_registry.py`,
`tests/arch/test_tenant_isolation.py`, `tests/arch/test_channel_gates.py`,
`specs/*.md`, `voiceai/common/*`.

## Data model

Collections `tenants`, `organizations`, `teams`, `memberships` (integrator
registers in `Collections`). Tenant-stamped rows; natural-key ids.
Backfill rewrites legacy string refs to hex (runbook below at integration).

## Security notes

- Slug→hex resolution happens server-side only; clients never mint ids.
- Membership reads scoped to the acting tenant (no cross-tenant
  enumeration — pinned by test).
- Suspended tenants: resolvers treat like disabled users (no principal).
- `make sec` clean (no outbound/shell/eval).

## Test plan

Agent A: entity CRUD units (conflicts, gates, fake store). Agent B:
resolver population (all three credential kinds), team-role gates,
validator retirement (no sync), me() tenant match. Agent C: backfill pure
rules (all collections, system passthrough, idempotence). Integrator:
full gate + migration dry-run on memory fixture.

## Verification

```sh
make check
make sec
```

## Rollout

Single merge; snapshot-first backfill per runbook below (integrator-written
from Agent C's report). Rollback = snapshot restore + revert (pre-identity
code ignores new collections; `org_id` strings keep working until T7).

## Runbook (integrator)

Snapshot-first, offline-verified before any live write (`make check` green is
the entry gate; the helpers are pure and import-safe):

1. **Snapshot.** Atlas snapshot / `mongodump`; record the id (spec-0020
   pattern). Rollback is restore + revert, nothing else.
2. **Default tenant.** `plan_default_tenant(existing_slugs)` (from
   `tooling/backfill_identity.py`) plans the row (`{}` = already present,
   no-op); insert it through `MongoAuthStore.save_tenant`, which pins `id`
   to the minted ObjectId hex (natural-key rule — callers never mint ids).
3. **Slug→hex map.** List tenants; map every legacy org slug to its tenant
   object hex (`{"default": <hex>, ...}`), resolved server-side only.
4. **Rewrite.** For every module collection, rewrite `tenant_id`/`org_id`
   (`REWRITABLE_FIELDS`) via `rewrite_value`: known slugs → hex; `"system"`,
   already-hex (24/32), `None`, and unknown slugs pass through untouched
   (unknown slugs are triage, not silent rewrites).
5. **Verify empty.** `audit_documents` over the collections must report `{}`
   afterwards; `needs_backfill` (spec 0020) must be empty outside
   `SKIPPED_COLLECTIONS`. Non-empty = stop, triage, re-run (idempotent).
6. **Census note.** `tenants`/`organizations`/`teams`/`memberships` ride
   `DEFAULT_STAMPED_COLLECTIONS` (spec-0038 precedent for new collections
   with no legacy rows — the rule never matches, harmless).

## Integration notes (integrator deviations, all loud)

- **Ticket tenancy.** `redeem_ticket` was a fourth credential path outside
  the three spec-owned resolvers; it now projects tenant/teams like them —
  otherwise the voice WS binding (`principal.tenant_id`) would strand calls
  on the system tenant. Pinned by `test_identity.py` (redemption) and the
  voice `binds_projected_tenant_not_org_slug` test.
- **Middleware.** No `app_factory.py` change was needed: Agent B's
  `resolve_request_identity` already binds `principal.tenant_id or
  principal.org_id` (hex-first with slug fallback — exactly the spec line).
  The switch landed in the two channel controllers that bypass the
  middleware (`voice/controller.py`, `chat/controller.py` ×2).
- **Audit stamp.** `audit()` stamps `subject.tenant_id`, falling back to
  `subject.org_id` — exactly what the spec-0020 backfill derives for the
  same row, so pre-migration events stay readable and the 0040 rewrite
  converts both sides together. `auth_events` filters on
  `principal.tenant_id`.
- **Service split.** The identity program grew `auth/service.py` to 1125
  lines (canonical budget 800, no debt escape): split into
  `service_base.py` (kernel: ctor, guards, minting, audit, tenancy
  projection), `service_admin.py` (invites/users/password/audit reads),
  `service_identity.py` (tenant/org/team/membership CRUD), with `service.py`
  keeping the credential facade (`AuthService` + `JwtSettings` +
  `SessionTokens` surface unchanged). Method bodies moved verbatim; the
  services layer gate now covers `service_*.py` too.
- **Constant promotion.** The private `_DEFAULT_TENANT_ID` became
  `common.constants.DEFAULT_TENANT_ID` (already the backfill's source).
- **Legacy port.** `test_module_def.py`'s isinstance pin was superseded by
  `test_service_seams.py`'s member-by-member check (legacy predates exactly
  the 11-method tail — `save/get_organization` predate the program and stay
  on the pre-identity surface); partial fakes cast at the boundary
  (`test_tenant_context.py` precedent).

## Burn-down

- [x] Agent A: identity entities + CRUD.
- [x] Agent B: auth runtime integration.
- [x] Agent C: migration + backfill.
- [x] Integrator: shared wiring + runbook + gate + commit.
