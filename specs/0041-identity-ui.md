# Spec 0041: identity management UI (Phase D UI track)

## Goal

Finish the Phase D UI: tenant/org administration, suspended-tenant UX,
team-role-aware gating, and invite-with-team-grant. What exists (do not
rebuild): `platform/identity.ts` hooks (`useMyTeams`, `useCreateTeam`,
`useAddTeamMember`, `useRemoveTeamMember`), `schemas/identity.ts` rows,
`org-team.tsx` Teams section (create + per-team grant + member lists),
`execution-drawer` recording display. Gaps below are the whole spec.

## Non-goals

- Backend changes (spec 0040 frozen; binding contract §Backend contract).
- Tenant switching (no backend API: sessions resolve from the user row —
  the UI never switches tenants, it administers the one the user belongs to).
- New design system work (existing section/form patterns reused).

## Backend contract (frozen by spec 0040)

- `POST /auth/tenants` (owner; slug unique → 409), `POST
  /auth/organizations` (owner; unknown tenant → 404 no-oracle), `POST
  /auth/teams`, `POST /auth/teams/{id}/members`, `DELETE
  /auth/teams/{team_id}/members/{user_id}`, `GET /auth/me/teams`
  (authenticated, tenant-scoped). Suspended tenant → resolvers yield no
  principal → 401s everywhere (indistinguishable from logged-out at the
  wire level — the UI distinguishes via a last-known-session hint, never by
  probing).
- `User.tenant_id` is the ObjectId hex; `org_id` deprecated but populated.

## UI contracts (binding — disjoint file sets per dev)

**Slice A — tenant/org admin (Dev A).** Files ONLY:
`src/services/platform/identity.ts` (append hooks),
`src/lib/schemas/identity.ts` (append input schemas),
`src/components/settings/org-identity.tsx` (new),
`src/app/team/page.tsx` (append the section below `OrgTeam` only),
`__tests__/services/identity-admin.test.ts` (new):
- Hooks `useCreateTenant` (`POST /auth/tenants`), `useCreateOrganization`
  (`POST /auth/organizations`) with `identityKeys` invalidation; owner-gated
  UI (global owner role — tenant creation is not a team grant).
- `org-identity.tsx`: current tenant display (slug + status from session
  tenant context where available, else a "pre-identity backend" note),
  organization create form (name → org under the acting tenant; the tenant
  id rides the session, never a picker), team list is NOT duplicated here
  (Teams section owns it).
- Tenant creation exposed only when no tenant context exists (bootstrap +
  multi-tenant provisioning); never a switcher.
- Mount: render the section below `OrgTeam` on `/team` (append-only edit to
  the page); no other page changes.

**Slice B — suspended UX + team-aware gating (Dev B).** Files ONLY:
`src/lib/rbac.ts` (extend, additive),
`src/components/layout/app-shell.tsx` (suspended banner only),
`src/app/login/page.tsx` (suspended copy only),
`__tests__/lib/rbac-teams.test.ts` (new):
- Suspended tenant: after login, repeated 401s with a last-known session
  render a "workspace suspended — contact your owner" banner + logout,
  never an infinite login redirect loop. Copy explicit, no probing.
- Team-aware gating additive: `useTeamRole(teamId)` (membership from
  `useMyTeams`, `team_roles` map where the session carries it) for
  team-scoped affordances; global `useCan` semantics UNCHANGED (backend
  re-checks everything). No existing gate may get stricter.

**Slice C — invite-with-team-grant + gate (Dev C).** Files ONLY:
`src/components/settings/org-team.tsx` (invite section only),
`__tests__/components/org-team-invite.test.tsx` (new), full gate owner:
- Invite form gains optional team-grant select (teams from `useMyTeams`;
  grant applied post-invite via membership add keyed by invite email).
- Backend completes it: `accept_invite` claims email-keyed pending grants
  by re-keying them to the minted `user_id` (integrator-added,
  `service_admin.py`; email keys can never collide with `usr_` ids).
  Until then the UI copy stays honest: grant is best-effort step two,
  re-grantable from Teams post-accept.
- Full gate at end: `npm run lint && npx tsc --noEmit && npm test` green.

## Test plan (UI track, jest)

Hook tests (mocked apiClient: 409 slug-taken, 404 unknown-tenant, admin
401s), suspended banner (mocked session + failing queries), invite-grant
flow (grant select → invite → membership add call shapes), RBAC
(team-role reads, global gates unchanged). Existing suites stay green.

## Verification

```sh
npm run lint && npx tsc --noEmit && npm test
```

## Rollout

UI-only, additive. Every section feature-detects the backend (404/500 on
identity endpoints → "unavailable on this backend" copy, the TeamsSection
`backendMissing` precedent) instead of version-gating.

## Burn-down

- [x] Slice A: tenant/org admin (Dev A).
- [x] Slice B: suspended UX + team-aware gating (Dev B).
- [x] Slice C: invite-with-team-grant + gate (Dev C).
- [x] Integrator: accept-time grant claim (backend) + gates + report (no commit — gate-held).
