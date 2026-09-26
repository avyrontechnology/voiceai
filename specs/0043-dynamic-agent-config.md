# Spec 0043: validated dynamic agent_config extension

## Goal

Tenants need per-agent custom call-behavior keys (tenant-specific flags, thresholds,
labels, experiment knobs) without a DB migration or a schema release per key, through
the existing VALIDATED write path (`PUT /agent/{id}` full replace and
`PATCH /agent/{id}` merge), with audit coverage. Spec 0042 proved the pain: live-but-
hidden legacy keys (`call_hangup_message`, `welcome_message_delay`, …) were unreachable
through validated writes, so Slice C had to promote each one to a first-class
`ConversationConfig` field by hand. This spec owns the GENERAL mechanism so the next
unknown key never needs a release: one reserved, validated namespace that rides the
opaque config envelope end to end, while everything outside it stays strict.

## Non-goals

- Promoting any specific key (0042 Slice C owns the known hidden keys; graduation of an
  extension key to first-class happens in a follow-up spec, never here).
- A top-level agent-wide `extensions` bag on `AgentModel` (see OPEN-1; this spec covers
  the per-task `task_config` scope only, where the 0042 precedent lives).
- Runtime consumption of extension keys (the engine ignores them; opt-in reads are a
  follow-up spec's consumer pins, never silent behavior changes here).
- Inbound engine, tool propagation, `agent_type`/channel work (specs 0045/0046/0047).
- UI implementation (UI track follows the builder contract notes; no UI files in this
  repo change here).
- Legacy `agent_manager/` edits: forbidden — no runtime seam needs touching because the
  engine already reads configs as opaque dicts.
- A new collection, index, migration, or backfill (see Data model: none).

## Decisions (approved)

1. **Namespaced passthrough (CHOSEN): one reserved `extensions` dict on
   `ConversationConfig`, free-form inside, validated outside.** Tenants put arbitrary
   custom keys under `task_config.extensions`; the schema validates the namespace's
   shape (key syntax, count/size bounds, JSON-only values) and the merge/audit layers
   treat it as one choke point. Everything outside `extensions` keeps today's strictness
   (`AgentModel`/`TaskPatch` stay `extra`-strict where they are; unknown top-level keys
   are still dropped-or-rejected exactly as today), so 0042's prove-or-remove guarantee
   is untouched: a key that wants runtime meaning still graduates to a first-class
   field in its own spec.
2. **Allowlist registry of known-dynamic keys (REJECTED):** a registry reintroduces the
   release bottleneck this spec exists to remove — every tenant key would need a
   registry entry plus a deploy, with registry-vs-code drift as a new failure mode.
   It also scatters merge/audit rules across N keys instead of one namespace, and it
   cannot serve tenant-defined keys (unknown at our release time) by construction.
   The registry pattern stays correct for provider/model catalogs (spec 0022), where
   the valid set is ours; it is wrong for tenant key names, where the valid set is
   theirs.
3. **Storage rides the existing opaque envelope** (`AgentDefinition.config:
   `dict[str, Any]`, byte-identical round-trips): `model_dump()` already carries
   `extensions` into the stored dict, so persistence, the tenant-scoped repository,
   and the engine seam need zero changes.
4. **Audit walk explicitly skips the `extensions` subtree** (catalog/provider
   validation never sees tenant keys), while a dedicated shape validator guards the
   namespace boundary. One exemption, named in code, pinned by the drift test.
5. **PATCH deletion inside `extensions` uses an explicit clear list**
   (`clear_extensions` on `TaskPatch`), consistent with the existing `clear` pattern —
   present-`None` stays a no-op everywhere including inside `extensions` (no
   null-means-delete exception to document or misread).

## Interface contracts (binding — disjoint file sets per agent)

**Slice A — schema owner.** Files ONLY:
`voiceai/modules/agents/constants.py` (extension constants only),
`voiceai/modules/agents/models/agent.py` (`ConversationConfig` only),
`voiceai/modules/agents/tests/test_extensions_schema.py` (new):

- Constants (Rule 1b — every literal named):
  `EXTENSIONS_KEY = "extensions"`, `EXTENSION_KEY_PATTERN` (`^[A-Za-z][A-Za-z0-9_]{0,63}$`
  — allowlist syntax; excludes `.`, `$`-prefix, `__proto__`/`constructor`/`prototype`
  by construction), `MAX_EXTENSION_KEYS` (32), `MAX_EXTENSION_VALUE_BYTES` (4096,
  JSON-serialized per value), `MAX_EXTENSIONS_TOTAL_BYTES` (32768), `EXTENSION_MAX_DEPTH`
  (3). Exact numbers are constants so tuning is not a code change.
- `ConversationConfig.extensions: dict[str, Any] = Field(default_factory=dict)` with a
  `@field_validator("extensions")` enforcing: key syntax (pattern above), key count,
  per-value JSON-serializability + byte cap, total byte cap, nesting depth cap.
  Violation messages name offending KEY names only, never values (audit opacity).
  `Any` carries an inline `# why:` comment (Rule 6).
- Default is `{}` so existing rows/dumps validate unchanged (additive).
- Schema unit tests: defaults (`{}`), each rejection class, round-trip
  (`model_validate` → `model_dump` preserves extension entries byte-identical),
  unknown top-level keys still dropped (today's strictness pinned: pydantic default
  `extra="ignore"` on `ConversationConfig`/`AgentModel` silently drops unknown keys —
  this spec changes nothing outside `extensions`).

**Slice B — pure merge + audit owner.** Files ONLY:
`voiceai/modules/agents/static_methods.py`,
`voiceai/modules/agents/tests/test_extensions_merge.py` (new):

- `audit_provider_config` gains the one named exemption: the walk never descends into
  an `extensions` subtree (task `task_config.extensions` skipped by key, not by
  value-sniffing). Documented inline with a `# why:`/spec reference.
- `apply_agent_patch` handles the dynamic section with existing semantics:
  `tasks_patch[].task_config.extensions` merges key-by-key via the existing `_merge_dict`
  recursion (present wins, present-`None` is a no-op); new `clear_extensions: list[str]`
  per task op drops named keys AFTER the merge; unknown clear names are problems
  (strict parity with `clear`); `tasks` wholesale replace needs no special handling.
  Unknown paths still reported, never applied.
- Pure tests: merge matrix (add/overwrite/no-op-null/nested), clear list (hit, miss →
  problem, empty → no-op), audit walk ignores `extensions` (a garbage provider name
  inside `extensions` produces NO problem; the same name outside still does).

**Slice C — service owner.** Files ONLY:
`voiceai/modules/agents/service.py`,
`voiceai/modules/agents/tests/test_extensions_service.py` (new):

- No new flow: create (`PUT`/`POST` full `AgentModel`), update, and `patch_agent`
  (load tenant-scoped → merge → full strict validate → catalog walk → save) already
  carry `extensions` through `model_dump()`/`model_validate()`. Slice C wires the
  failure mapping only: `ValidationError`s rooted at `extensions` surface as
  `AgentConfigInvalidError` with key-names-only problems (never values, never raw
  `str(exc)` payloads beyond the existing envelope), and failed validation still
  writes nothing (atomicity, per the existing patch test).
- Tenant isolation is inherited, not added: loads/saves go through the tenant-scoped
  definition store (Phase D tenant-hex scoping — `tenant_id` carries the tenant
  ObjectId hex per `voiceai/modules/auth/models/tenant.py`, spec 0040); extension keys
  never leave their row and the tenant-keyed runtime reader (`runtime/compiled.py`
  unscoped-rejection guard) is untouched.
- Service tests with DI fakes: PUT round-trip preserves extensions; PATCH merge +
  clear round-trips; invalid extensions abort before any write; cross-tenant read of
  another tenant's extension keys is impossible through the scoped store (row-level —
  no extension-specific oracle).

**Slice D — wire-contract owner.** Files ONLY:
`voiceai/modules/agents/schemas.py`,
`voiceai/modules/agents/tests/test_extensions_controller.py` (new):

- `TaskPatch` gains `clear_extensions: list[str] = Field(default_factory=list)`
  ("explicit per-key drops inside `task_config.extensions`; present-`None` is a
  no-op, never a delete"). `task_config: dict[str, Any] | None` stays as-is (key-by-key
  merge already covers `extensions`). `PatchAgentRequest` stays `extra="forbid"`;
  `controller.py` is UNCHANGED (the `Create`/`Patch` aliases flow the new fields
  through; no handler logic changes — Rule 1f).
- Controller tests through the real app factory (`httpx` ASGI transport): PATCH with
  `tasks_patch[].task_config.extensions` merges; `clear_extensions` drops; invalid key
  syntax answers 400 with the opaque envelope (error id logged, no value echo).

**Slice E — drift-pin + docs owner.** Files ONLY:
`tests/arch/test_dynamic_config.py` (new), `openapi.yaml`, `API_REFERENCE.md`:

- Mechanical pins: `extensions` is the ONLY free-form subtree inside the validated
  agent schema (allowlisted `path → exemption` map in the test; the test fails on any
  second passthrough AND on any catalog/audit read descending into `extensions`).
  Every other `ConversationConfig` field keeps its 0042 Slice D consumer-or-absent pin.
- Docs: `extensions` syntax, bounds (pointing at the constants, not duplicating
  numbers), PATCH merge + `clear_extensions` semantics, graduation path
  (prove-or-promote: a load-bearing extension key graduates to first-class in its own
  spec), builder contract pointer.
- `specs/0043*` stays integrator-owned (no agent edits).

## UI contracts (UI track, separate repo — for the record, not this build)

- Builder agent editor gains a namespaced key/value section bound to
  `tasks[i].task_config.extensions` per conversation task: key input constrained to
  the allowlist syntax (mirror `EXTENSION_KEY_PATTERN` client-side for fast feedback;
  server remains authoritative), JSON value editor with the byte caps surfaced, per-key
  delete emitting `clear_extensions` (never null-writes), full-task replace unaffected.
- First-class fields and `extensions` never share a key name (client-side collision
  warning; server wins silently by namespace separation — no merge across the boundary).
- Schemas in `lib/schemas/agent.ts` mirror `extensions: Record<string, unknown>` with
  the same bounds; no other builder contract changes.

## Data model

None new. `AgentDefinition.config` is already `dict[str, Any]` stored opaque
(`voiceai/modules/agents/models/definition.py`) with byte-identical round-trips, and
`model_dump()` carries `extensions` into that dict — so no collection, no field
migration, no index, no backfill. Existing rows validate with `extensions == {}`.
Deletes stay soft (`is_active=False`, unchanged). `BaseFields`/`Collections` untouched.

## Security notes

- **Key-name injection:** allowlist regex `^[A-Za-z][A-Za-z0-9_]{0,63}$` forbids dotted
  keys (no dot-path traversal/confusion in dot-notation consumers), `$`-prefixed keys
  (no Mongo operator injection via the stored dict), and `__proto__`/`constructor`/
  `prototype`-style names (no prototype-pollution on JSON round-trips to JS
  consumers). Enforcement at the schema validator (Slice A) — untrusted input never
  reaches storage unexamined (boundary validation, AGENTS.md §4).
- **Oversize values:** per-value (4 KiB serialized), total (32 KiB), count (32 keys),
  depth (3) caps bound memory/CPU on validate/dump/log paths (unbounded growth is a
  bug, §5); all caps are constants, not literals.
- **Log redaction:** extension keys AND values never log at INFO (identifiers only —
  agent id + tenant id, Rule 3 + §4 PII rule); validation failures log key names only;
  `redact_secrets` before dumping any mapping that could carry the namespace.
- **Per-tenant isolation:** custom keys inherit row-level tenant scoping (Phase D hex
  model — `tenant_id` is the tenant ObjectId hex; loads/saves bind the request tenant;
  the runtime reader rejects unscoped construction). No cross-tenant key namespace
  exists; enumeration oracles are row-gated, unchanged.
- **Audit opacity:** 400 problems name key names + the violated rule, never values and
  never raw exception text; unexpected failures keep the opaque 500 + `error_id`
  envelope (the controller's swallowed-404 quirk is untouched).
- **Abuse:** mutating endpoints keep existing auth gates (service-layer authorization,
  unchanged); no new endpoints, no new outbound calls, no redirects, no new
  dependencies — `make sec` scope is the touched files only. No SSRF surface (no URLs
  are special inside `extensions`; a URL-valued extension is inert data until a
  follow-up spec gives it a consumer with `is_safe_outbound_url` + timeout).

## Test plan

- Slice A: schema units (defaults, every rejection class, round-trips, unknown-keys-
  still-dropped pin). Slice B: pure merge/clear matrix + audit-exemption pins (fakes,
  no I/O). Slice C: service units with DI fakes (PUT/PATCH round-trips, atomicity on
  invalid extensions, tenant-scoped isolation). Slice D: controller tests through the
  real app factory (`httpx` ASGI transport, offline only). Slice E: arch drift pins
  (sole-passthrough + no-audit-descent) + docs match. Integrator: full gate + coverage
  on touched modules. New behavior without a test does not exist; coverage ≥ 85% on
  touched modules.

## Verification

Commands the integrator runs (default):

```sh
make check
make sec
```

Plus spec-specific: `make cov` (≥ 85% on `voiceai/modules/agents/...` touched files),
and the Slice E drift test explicitly:
`.venv/bin/python -m pytest -q tests/arch/test_dynamic_config.py`.

## Rollout

Additive-only, no flags, no dual-mount, no migration: existing rows read/write
unchanged (`extensions` defaults to `{}`; dumps without the key validate clean).
Rollback is a revert — rows that stored extension keys keep them opaque in `config`
(the envelope is schemaless at rest); pre-rollback code drops them on validate, which
is the documented pre-0043 behavior, not corruption. No shim burn-down entries (no
legacy file becomes a re-export; engine seam untouched). UI binds in its own track
afterwards. Graduation path (not this rollout): a follow-up spec promotes a proven
extension key to a first-class field and migrates its readers — 0042 Slice C is the
template.

## Burn-down

- [ ] Slice A: schema — constants + `ConversationConfig.extensions` + schema tests.
- [ ] Slice B: pure merge + audit exemption — `static_methods.py` + merge tests.
- [ ] Slice C: service failure-mapping + isolation — `service.py` + service tests.
- [ ] Slice D: wire contract — `schemas.py` (`clear_extensions`) + controller tests.
- [ ] Slice E: drift pins + docs — `tests/arch/test_dynamic_config.py`, `openapi.yaml`,
  `API_REFERENCE.md`.
- [ ] Integrator: drift resolution + gate (`make check`, `make sec`, `make cov`) +
  report (no commit — gate-held).

## Open decisions (OPEN — integrator/architect to close before build)

- **OPEN-1:** Should a follow-up spec add an agent-wide (non-task) `extensions` bag on
  `AgentModel` for keys that are not per-task behavior (e.g. tenant labels, routing
  hints)? This spec deliberately scopes to `task_config` where the 0042 precedent
  lives; agent-wide keys can ride `AgentDefinition.config` top-level today only
  unvalidated (dropped on write) — closing OPEN-1 means either a second namespaced
  field or an explicit wont-do.
- **OPEN-2:** Exact bound values (`MAX_EXTENSION_KEYS=32`, per-value 4 KiB, total
  32 KiB, depth 3) are starting points in constants — confirm against the largest
  known tenant ask before build; tuning is a constant change, not a spec change.
- **OPEN-3:** Consumer-read API for extension keys (an opt-in runtime accessor with
  per-key allowlist + audit pin) belongs to the first spec that gives an extension key
  runtime meaning — acknowledged here so no slice improvises one.
