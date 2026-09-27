# Spec 0046: shared-tool live-link semantics (backend)

## Goal

Close the copy-at-write gap: when a shared tool changes, attached agents pick
it up (the approved live-link decision) instead of silently running stale
snapshots. Edits propagate via version-stamped re-materialization; deprecation
becomes a hard stop with a migration path; the unusable seeded webhook gets a
real endpoint or is unseeded; embedded-URL SSRF bypass closes to parity with
the ref path.

## Non-goals

- UI work (UI track binds afterwards; picker live-vs-pinned badges noted, not built).
- New tool kinds, new endpoints, new collections.
- Runtime (call-time) ref resolution inside the voice hot path — propagation
  happens at write/read boundaries, never per-turn.
- Legacy `agent_manager/` edits (forbidden).

## Decisions (approved)

1. **Version-stamped materialization + cascade on edit.** Tool rows carry
   `tools_version` (exists); agent rows record source versions beside
   materialized tools. `update_tool` bumps the version AND re-materializes
   every attached agent (bounded lookup by `tool_refs`, same tenant) in the
   same service call — reads keep serving the stored snapshot, so the hot
   path never gains a lookup.
2. **Deprecation is a two-stage hard stop.** `deprecated=True` blocks NEW
   attaches immediately (400, naming the tool) while already-attached agents
   keep running + get flagged (`stale_deprecated` surfaced on read); a later
   spec deletes. `delete_tool` is refused while references exist (409 with
   the referencing agent count) — no silent cascade-detach.
3. **Seed fix.** `webhook:pre_call_notify` gets a real endpoint or is
   unseeded (no attachable-but-broken rows). Decision in-slice, loud.
4. **SSRF parity.** Embedded (non-ref) `url`/`tools_params.url` values pass
   the same `_is_url_safe` gate at attach time that refs pass (grandfathered
   rows keep running until re-saved — no retroactive breakage).

## Interface contracts (binding — disjoint file sets per agent)

**Slice A — tools service owner (Agent 1).** Files ONLY:
`voiceai/modules/tools/service.py`,
`voiceai/modules/tools/tests/test_livelink.py` (new):
- `update_tool` bumps `tools_version`, re-materializes attached agents
  (lookup by ref, tenant-scoped, bounded), returns the propagation count.
- `delete_tool` refuses referenced rows (409 + count); `deprecated` blocks
  new attaches (400).
- Seed decision (fix endpoint or unseed) + seeder change if needed
  (`seed.py` is Slice A-owned too — append, don't reformat).
- Unit tests with DI fakes: propagate count, refuse-delete, deprecation
  gate, seed shape.

**Slice B — agents re-materialize path (Agent 2).** Files ONLY:
`voiceai/modules/agents/service.py` (re-materialize helper + read flag only),
`voiceai/modules/agents/tests/test_livelink.py` (new):
- `_resolve_tool_refs` gains an idempotent re-materialize entry used by
  both attach-time and Slice A's cascade (single code path, no fork).
- Reads flag stale materializations (`stale_deprecated` + version drift
  surfaced, never silently re-written on read).
- No other `service.py` behavior changes (grep-verified minimal diff).

**Slice C — SSRF parity (Agent 3).** Files ONLY:
`voiceai/modules/agents/service.py` (attach-gate lines only — coordinate
  with Agent 2: adjacent regions, non-overlapping hunks; integrator merges),
`voiceai/modules/agents/tests/test_ssrf_parity.py` (new):
- Embedded urls pass `_is_url_safe` at attach; grandfathered rows untouched
  until re-saved; SSRF failures are 400s naming the path, never the URL.

**Slice D — pins + docs (Agent 4).** Files ONLY:
`tests/arch/test_tool_livelink.py` (new), `openapi.yaml`,
`API_REFERENCE.md`, `voiceai/modules/tools/CONTRACT.md` (append version
  section only):
- Mechanical pins: version bump on edit, cascade count, refuse-delete,
  deprecation gate, SSRF-gate call sites (both paths), seed shape.
- Docs match code at gate time.

## UI contracts (UI track, separate repo — for the record, not this build)

- Picker shows live vs stale-pinned badges (`stale_deprecated`, version
  drift) from read flags; deprecation blocks new attaches client-side with
  the 400 copy; delete-refused dialog lists the count. Schemas mirror new
  fields. No other builder changes.

## Data model

No new collections. `tools_version` already exists (bump discipline is new);
agent rows gain source-version stamps beside materialized tools (schema
change, additive, existing rows read as unversioned-legacy = always fresh
until first re-materialization). Soft deletes unchanged.

## Security notes

- SSRF gate on both paths (`is_safe_outbound_url` + timeouts, off-loop);
  failures opaque (path named, URL never echoed).
- Cascade is same-tenant only (scoped lookups; no cross-tenant fan-out).
- Version/drift flags are read-only metadata, never trust roots.
- `make sec` clean; no new deps.

## Test plan

Slice A: service units (fakes). Slice B: re-materialize + flag units.
Slice C: SSRF parity matrix (ref/embedded/grandfathered). Slice D: arch
pins + docs match. Integrator: full gate + coverage.

## Verification

```sh
make check
make sec
```

## Rollout

Additive behavior behind existing endpoints; propagation is synchronous in
`update_tool` (bounded fan-out, same-tenant). Rollback is revert (stale
snapshots keep serving — the pre-0046 posture). UI binds afterwards.

## Burn-down

- [x] Slice A: tools service livelink (Agent 1).
- [x] Slice B: agents re-materialize path (Agent 2).
- [x] Slice C: SSRF parity (Agent 3).
- [x] Slice D: pins + docs (Agent 4).
- [x] Integrator: drift + gate + report (no commit — gate-held).

## Integration notes (integrator resolutions, all loud)

- **Wired the cascade end to end.** Slices shipped the seam (Protocol),
  the helper, and the gate, but nothing connected them: the attach path
  still called `get_tool` (deprecation gate dead), the controller still
  unpacked one value (runtime crash on update), and no `agent_links`
  adapter existed. Integrator: attach/webhook resolves now go through
  `get_tool_for_attach` (400 naming the path); controller unpacks the
  `(saved, propagated)` tuple; `_AgentToolLinks` in `core/container.py`
  closes over the scoped definitions port + a `_tools`/`_logger`-only
  `AgentService` shim (chain verified attribute-clean; container cascade
  test pins it — any future chain drift fails there, not in prod).
- **Split `agents/service.py` (990 → ~640 + `service_tools.py`).** The
  slices pushed the canonical file past 800 with no debt escape in
  `module_shape`: the whole tool-ref machinery (resolve/materialize/
  webhook/stamp/flags/gate + SSRF seam + keys) moved verbatim to
  `AgentToolsMixin`; surface unchanged. Seam test patch-targets (5 files)
  + arch pins repointed to the new home.
- **Fixed parallel-edit casualties:** Agent A deleted `RAG_CACHE_TTL_KEY`
  (restored); Slice D xfail markers dropped after XPASS; seed pin accepts
  the deprecated outcome; docs-match pairs repointed.
- **Budgets ratcheted with citations:** agents 16200→17100 (split
  overhead ~130 lines of headers/imports/docstrings).
- Full gate green (1837 passed, coverage 97.6% on touched scope).
