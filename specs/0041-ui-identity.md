# Spec 0041: team + membership management UI (UI track, Phase D)

## Goal

The Team page (`/team`, `OrgTeam`) manages the spec 0040 identity program:
list the caller's teams, create teams under the acting org, and grant/revoke
per-team roles — against the six identity endpoints, with the same
react-query + zod discipline as the voices/catalog tracks. Global login,
signup, invite-accept, and role flows stay exactly as they are.

## Non-goals

- Tenant/org creation screens (no list/read endpoints back them — creation
  stays API-only until a read surface exists; documented, not deferred
  vaguely).
- Per-team billing/quotas; SSO/SCIM; team-scoped API keys (backend has none
  either — later specs).
- Sub-accounts (separate legacy surface, untouched).
- Changing `useCan` semantics: gates stay global-role until the backend
  enforces per-team roles (see below).
- Builder `channels` input: text forms already emit `["chat"]` via the
  transform (verified — back-compat default, no UI control needed).

## Backend contract (specified by spec 0040 + in-flight code; UI binds only after Agent A lands)

- `GET /api/v1/auth/me/teams` → `{ok, data: [...]}` teams of the caller in
  the acting tenant (rows `{team_id, org_id, tenant_id, name, ...}`; BaseFields
  extras stripped client-side).
- `POST /api/v1/auth/teams` with `{org_id, name}` → 201 (admins only).
- `POST /api/v1/auth/teams/{team_id}/members` with `{user_id, role}` → 201
  (admins only; duplicate `(user_id, team_id)` → 409).
- `DELETE /api/v1/auth/teams/{team_id}/members/{user_id}` → `{state}`
  (admins only; foreign rows read as missing).
- `POST /api/v1/auth/tenants`, `/auth/organizations` exist but have no UI
  surface (see Non-goals).
- Errors ride the standard envelope: 401 anonymous, 403 non-admin/owner or
  suspended tenant, 404 foreign ids (no oracle), 409 duplicate membership or
  slug. No new envelope shapes.
- Precondition: Agent A service CRUD (`create_tenant`,
  `create_organization`, `create_team`, `add/remove_membership`,
  `list_user_teams`) must land first — the controllers call methods that do
  not exist yet, so all six routes answer opaque 500s until then. The UI
  treats 500s as backend failures (existing global toast), never as
  feature absence; no fallback or version-gating is built for this track.

## UI contracts

- New `src/services/platform/identity.ts` (`identityKeys` + hooks following
  the `voices.ts` react-query pattern, `staleTime: 60s`, invalidation on
  membership/team mutations): `useMyTeams()` (`GET me/teams`),
  `useCreateTeam`, `useAddTeamMember`, `useRemoveTeamMember`. Tenant/org
  hooks are NOT added (no screens consume them).
- New zod schemas in `src/lib/schemas/identity.ts` mirroring the backend
  rows (tenant/org/team/membership round-trips, talko parity style);
  `tenant_id` passthrough already exists on the session user and stays
  unread (carried, never branched on).
- Team page bindings (existing `OrgTeam` sections, don't restyle):
  - "My teams" section listing `useMyTeams` rows (name, org, per-team role
    badge from the membership map where the row carries it, else global
    role); empty state points at team creation for admins.
  - Team create (admin-gated, existing `team.manage` action): org defaults
    to the acting org (no org picker — single-org reality; multi-org picker
    waits for an org list endpoint).
  - Member grant/revoke per team (admin-gated): user picker from the existing
    users query, role select (`member` default; owner/admin grant follows the
    existing owner-only invite rule — only owners may grant owner/admin,
    mirroring the invite flow), 409 duplicate surfaces inline ("already a
    member") instead of a dead form.
  - Invite flow unchanged; grant happens post-accept via membership add
    (documented in the section hint text — the invite endpoint takes no
    team parameter).
- RBAC: `useCan` keeps global-role semantics (`team.manage` = admin covers
  every new mutation, matching today's server gates). Per-team-role
  enforcement UI waits for backend enforcement — gating on data the server
  ignores would hide actions the server allows and show ones it 403s. The
  global `MutationCache.onError` toast stays the backstop for 403s.
- 400/403/404/409 handling: 409 renders inline on the grant form; 403
  renders the shared forbidden notice; 404s never distinguish foreign from
  missing (no oracle in the UI either).

## Test plan (UI track, jest)

Hook tests with mocked `apiClient` (shapes parse; 403/404/409 propagate;
mutations invalidate `identityKeys`); schema round-trips (tenant hex,
prefixed ids, membership uniqueness is server-side — client sends, never
validates); Team section interaction test (create team → appears; grant
member → role badge; duplicate grant → inline 409; remove → gone).

## Verification

```sh
cd ../voiceai-ui && npm test -- identity && npm run lint && npx tsc --noEmit
```

Plus a live pass against a seeded backend (default tenant + one team):
my-teams lists, create/grant/remove round-trip, duplicate 409 inline.

## Rollout

UI-only, additive. Team page works unmodified against backends without the
identity endpoints only insofar as the new sections stay hidden — the
section renders behind the same `team.manage` gate and shows the
backend-missing notice on 404/500 (feature-detect, don't version-gate).
No migration (new collections, server-seeded default tenant).

## Burn-down

- [ ] Identity service + schemas + Team bindings + 400/403/404/409 rendering.
- [ ] Live verification pass + this spec's boxes.
