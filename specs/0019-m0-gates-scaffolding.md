# Spec 0019: M0 mechanical gates and scaffolding (platform-core)

## Goal

Land the blueprint's M0 step: size budgets, tenancy tripwire, no-parallel-impl
ledger, import-cycle check, `ModuleDef.max_lines`, `make docs`, test-tree fold,
duplication detector with exemptions, per-module test/coverage targets, and
per-module CI jobs. Gates green on unchanged behavior — M0 adds constraint
machinery only, zero runtime changes.

## Non-goals

- No `voice/` moves, no tenancy enforcement beyond the tripwire (§B), no
  `make migrate` / `make evals` (no migrations or evals exist yet — targets
  without callers), no UI track, no `stash@{0}` files (`quickstart_server.py`,
  `platform/router.py` — another session's in-flight work, hands off).
- No behavior change anywhere: every edit here is gates, tooling, docs, or
  registry metadata.

## Interface contracts

### A. `tests/arch/test_size_budgets.py` (new gate)

- Any file under the new-arch roots (`common`, `core`, `database`, `modules`)
  or `tooling` over 1,500 lines fails, no exemptions. Legacy trees are exempt
  from the hard ceiling (they shrink through their migration specs per
  principle 08) but their over-800 files still need debt entries.
- Any file over 800 lines fails unless listed in `SIZE_DEBT` with an owning
  spec id. Initial debt (measured from today's tree, 10 files):
  `agent_manager/task_manager.py` (2498) + `voice/session` turn files
  (`transcript_listener` 1266, `switcher` 1132, `generation` 997,
  `history_sync` 968, `s2s_runner` 885) → spec 0024;
  `platform/router.py` (1326) + `platform/store.py` (948) → spec 0025;
  `helpers/utils.py` (1235) + `voice/tts` kalpa (878) → spec 0022.
- Module totals ≤ registry `max_lines`. Initial ceilings (measured + headroom,
  from today's tree): health 1500, agents 14000, auth 8000, wallet 6000,
  voice 46000 (explicitly interim — M2/M3/M5 split it; the ceiling only ratchets
  down). Totals count all `.py` under the module dir, tests included.

### B. `tests/arch/test_tenant_isolation.py` (new gate, tripwire scope)

Full enforcement is M1's job. M0 only guarantees no silent drift:
- Files under `voiceai/{common,core,database,modules}` containing `tenant_id`
  must equal `TENANT_ID_FLAGGED` (empty today — only the scaffolder template
  mentions it; any M1 file updates the set loudly in the same spec).
- No `t:{` tenant-key f-strings exist today; `common/keys.py` is created with
  `tenant_key(namespace, …)` + unit test so the one true key builder exists
  before anyone needs it.
- Provider-key reads stay under the existing `ENVIRON_FLAGGED_FILES` pattern
  (no new mechanism; referenced, not duplicated).

### C. `tests/arch/test_no_parallel_impl.py` (new gate)

Retired-concern ledger starting empty (`RETIRED = frozenset()`): for each entry
the legacy path must be a tagged shim or absent. Mechanism + vacuous pass; M2+
fills it. Asserts the ledger entries (not the tree) so adding a retirement
without its proof fails loudly.

### D. `tests/arch/test_import_cycles.py` (new gate)

AST import graph over `voiceai/{common,core,database,modules}` (stdlib/external
ignored), assert acyclic. Expected green day one (layer matrix already
enforced); any red is treated as a design bug to fix in this spec, never an
allowlist entry.

### E. `ModuleDef.max_lines` + registry

Additive `max_lines: int` field (defaults keep all 5 `MODULE` defs compiling;
values per §A). `test_registry.py` extended: budget present and positive.
`OWNERSHIP.md` regenerated via `make docs`.

### F. `make docs`

`voiceai/tooling/render_docs.py` generating `docs/ARCHITECTURE.md` (module rows
from the registry + static kernel table) and `docs/OWNERSHIP.md` (from the
registry). Docs gate = smoke test (generator runs, both files contain expected
section markers) — NOT a diff gate, because outputs stay gitignored and CI
cannot diff ignored files.

### G. Test-tree fold, `make dup`, per-module targets, CI

- Move `tests/arch/modules/test_registry.py` → `tests/arch/test_registry.py`;
  delete the empty dir (the only file in that tree; registry coverage is
  foundation, not single-module).
- `make dup`: normalized block-hash detector (30+ identical lines across
  modules) with `DUP_DEBT` exemption generated from today's tree at build time
  (verbatim parity delegators *will* trip a naive detector — hence exemption,
  not silence). Implemented as `voiceai/tooling/dup_blocks.py` + thin Makefile
  target; gate test asserts the detector runs (debt list is data, not logic).
- `make test MODULE=<name>` and `make cov MODULE=<name>` (≥85% per module;
  global `cov` keeps its spec-0004 deselects).
- CI: per-module jobs in `.github/workflows/modules.yml` (native path filters +
  matrix, no external actions — robust over clever); PR triggers unchanged.

## Data model

None. No collections, no migrations, no schema changes.

## Security notes

- No auth/secrets/PII surface changes. New code is gates + tooling + docs.
- Supply chain: no new dependencies of any kind (CI uses only actions already
  pinned in the repo workflows).

## Test plan

- New gate files ship with their own allowlists generated from the tree (not
  hand-written): size debt (10 files), dup debt (measured), tenant tripwire
  (empty sets verified by the test itself).
- Every new gate fails loudly on first drift (add-tenancy-file, grow-a-file,
  retire-without-proof, introduce-a-cycle).
- Full `make check` green; per-module `make test MODULE=<each>` green.

## Verification

```sh
make check
make sec
make docs
make dup
make test MODULE=agents
make cov MODULE=health
```

## Rollout

Single merge (platform-core file set only). No flags, no migrations, no
runtime behavior change by construction — rollback is a revert.

## Burn-down

- [x] A: size gate + SIZE_DEBT + max_lines + registry test
- [x] B: tenancy tripwire + common/keys.py
- [x] C: no-parallel-impl ledger
- [x] D: import-cycle gate (broke the real agents-models cycle via models/base.py)
- [x] E: registry + OWNERSHIP regen (covered by A/F)
- [x] F: render_docs + smoke gate
- [x] G: fold + dup (+DUP_DEBT) + MODULE targets + CI jobs
