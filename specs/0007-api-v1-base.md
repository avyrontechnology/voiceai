# Spec 0007 — `/api/v1` base for all HTTP APIs

- **Status:** done (dual-serve live both repos; bare retirement is a later spec)
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** spec 0006 (done — auth cut over at `/api/v1/auth`)

## Goal

Every HTTP API answers under `/api/v1` while bare paths keep serving (dual-serve —
the E2 precedent; NOT a flag-flip, never a second `/auth` 404 incident). The UI
migrates to `/api/v1` + auth-envelope unwraps in the same change. Bare-path
retirement is a later spec, never this one.

## Non-goals

- Enveloping legacy routers (prefix-only move; shapes byte-identical on both mounts).
- Websockets (`/chat/v1/*`, telephony legs) — not REST APIs, untouched.
- talko/twilio/plivo services — separate containers; dual-serve keeps them safe.
- Bare-path deletion — explicitly out; a later spec owns the sunset.

## Design

- **Backend (voiceai repo):** every router mounted twice — bare (today) + `/api/v1`
  (new): the `build_routers()` loops in `local_setup/quickstart_server.py` and
  `voiceai/platform/router.py::create_platform_app`, plus stacked decorators on
  quickstart's 6 direct routes (`/all`, `/agent/{id}`, …). One shared constant
  `API_PREFIX = "/api/v1"` (already in `voiceai/common/constants.py` — reuse it).
  Duplicate operation_ids across the two mounts are tolerated (FastAPI warns only).
- **UI (voiceai-ui repo):** single prefix point inside `lib/api-client.ts::apiClient`
  (`API_V1 = "/api/v1"` prepended to every endpoint) + auth-envelope unwraps in
  `services/auth.ts` (parse `raw.data` with the existing zod schemas; error path
  already reads `detail`, which envelopes preserve). Direct-`fetch` sites
  (auth-navbar) migrated by hand. Unit tests mocking `apiClient` are unaffected
  (they assert pre-prefix endpoints).

## Interface contracts

- `GET /api/v1/all` ≡ `GET /all` (status + body); same for every router in
  `build_routers()` and the 6 direct routes. Parity is asserted per-route-group,
  not per-route (representative paths + full route-table 1:1 set comparison).
- UI: `apiClient("/auth/me")` requests `/api/v1/auth/me`; hooks return the same
  frontend types as today (unwrap inside, types unchanged).

## Data model

None.

## Security notes

- Auth cookies/scopes unchanged; no new surface (same handlers, second prefix).
- CORS/origins unchanged.

## Test plan

- Backend: arch test mounts both, asserts route-table parity (every bare path has
  a `/api/v1` twin) + representative body-equality probes (auth me, /all, /wallet,
  one write). Full backend suite green (bare assertions untouched and passing).
- UI: jest suite green; auth-hook tests assert unwrapped types against enveloped
  fixtures (add where missing).

## Verification

- Backend: `make check` equiv (ruff + strict arch lint + mypy + `pytest tests/arch`),
  `make test-all` same-8, `make sec`.
- UI: repo's own lint/test commands per its AGENTS.md (agent reads them first).

## Rollout

Two commits (backend dual-serve, UI migration), each green and independently
revertible; either order is safe (bare never breaks). Bare retirement later.
