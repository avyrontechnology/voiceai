# Spec 0030: tool picker + attach UX (UI track, Phase B slice 3)

## Goal

The agent builder offers shared tools and webhooks from the registry:
browse system + tenant tools, attach by id, wire webhook refs per tool —
never retyping URLs. Legacy per-agent tool calls retire.

## Non-goals

- Builder layout redesign (bind existing tool sections, don't restyle).
- Per-agent override merge UI (overrides stay in `tools_params` config as
  today; refs carry no overrides by design).
- Inbound triggers; admin tool CRUD; BYOK key inputs.

## Backend contract (frozen by spec 0029, slices 1–2)

- `GET /api/v1/tools` → `{ok, data: {tools: [...]}}`, rows
  `{tool_id, kind, name, description, parameters, url, method, auth_ref,
  timeout_s, deprecated, tools_version, tenant_id, ...}`; `?kind=` narrows.
- `GET /api/v1/tools/{id}` → row or 404 (foreign reads as missing).
- `POST /api/v1/tools` → 201 (tenant rows; `internal` kind 403).
- `PUT /api/v1/tools/{id}` → replace (own tenant only; system 403).
- `DELETE /api/v1/tools/{id}` → `{state: "deleted"}` (system 403).
- Agent write accepts `tool_refs[]` + `pre_call_webhook_ref` per
  `tools_params` entry; failures answer 400 with `details.problems[]`
  (paths + valid values) — the forms render these verbatim.
- List merges system + tenant with tenant winning id ties.

## UI contracts

- New `src/services/platform/registry-tools.ts` (leave the legacy
  `services/platform/tools.ts` untouched until the retirement slice):
  `registryToolKeys` + hooks (`useRegistryTools(kind?)`,
  `useRegistryTool(id)`, `useCreateRegistryTool`,
  `useUpdateRegistryTool`, `useDeleteRegistryTool`) following the
  `voices.ts` react-query pattern (`staleTime: Infinity` for system rows;
  invalidate on tenant mutations).
- New zod schemas in `src/lib/schemas/tools-registry.ts` mirroring the
  backend shapes (round-trip test against example payloads, talko parity
  style).
- Builder bindings (existing tool sections in the agent form):
  - Tool picker: browse registry (system badge vs tenant badge),
    deprecated rows hidden unless already attached (grandfather UX);
    attach appends the id to the agent's `tool_refs` (PATCH).
  - Webhook attach: webhook-kind rows listed with URL summary;
    per-tool `pre_call_webhook_ref` dropdown inside the tool-config
    section (replaces raw URL typing; explicit URL field remains and
    wins when filled — mirrors the backend override rule).
  - Detach removes the id (embedded materialized entries persist until
    the next save re-resolves — document in the picker hint text).
- 400 handling: `problems[]` entries render beside the picker
  (`tasks[N].api_tools` path → tool section).
- Retirement: after verification, `services/platform/tools.ts` (legacy
  per-agent calls) deletes with its tests; the picker is the only path.

## Test plan (UI track, jest)

Hook tests with mocked `apiClient` (shapes parse; 403/404 propagate);
schema round-trips; picker interaction test (attach by id → PATCH body
contains the ref; detach removes it; deprecated hidden unless attached).

## Verification

```sh
cd ../voiceai-ui && npm test -- tools && npm run lint
```

## Rollout

UI-only, additive (new service file beside the legacy one). Builder works
unmodified against backends without the tools module (hooks degrade to the
legacy per-agent calls when `/tools` 404s — feature detect, don't
version-gate). Legacy retirement is a separate commit after verification.

## Burn-down

- [x] Registry hooks + schemas + picker bindings + 400 rendering.
- [x] Legacy tools-calls retirement (done inline, not as a follow-up —
  deviation from the rollout plan, recorded here: the legacy `/tools`
  routes are unmounted on prod and prod Redis holds zero legacy rows,
  so a degrade-to-legacy path would be dead code. The UI rewrites
  `services/platform/tools.ts` in place instead of adding
  `registry-tools.ts` beside it; `src/lib/schemas/tools-registry.ts`
  likewise folds into `src/lib/schemas/platform.ts`. All behavioral
  contracts above hold: system/tenant badges, grandfathered deprecated
  display, attach-by-id PATCH with full-array bodies, webhook-ref
  dropdown plus per-attachment params editor, `.api_tools` 400 rendering.
  Form-state plumbing (`agent_config.api_tools` round-trip) additionally
  guarantees PATCH attachments survive the next PUT save — without it,
  full-overwrite saves would evaporate attached refs.)
