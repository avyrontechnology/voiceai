# Spec 0010: Repo setup for 100-engineer parallel delivery

## Goal

Make the repository self-describing and mechanically enforced so ~100 engineers
across squads can scaffold, build, and ship modules without collisions: one module
template, ownership in the `ModuleDef` registry (no CODEOWNERS files per team
decision), required CI equal to `make check`/`make sec`, and per-module
`CONTRACT`/`RUNBOOK`/`README` colocated with the code.

## Non-goals

- No runtime behavior change. No new product modules (telephony split, channels,
  campaigns) — those get their own specs on top of this scaffold.
- No deployment topology change (single VM + Redis stays; Postgres/ClickHouse/K8s
  remain future interfaces, not implementations).
- No legacy engine rewrite; strangler shims stay untouched.

## Interface contracts

Changed files (disjoint waves):

- Wave 0: `specs/*`, `docs/*`, `scripts/scaffold_module.py`,
  `voiceai/modules/__init__.py` (owner fields), `AGENTS.md` (Rule 1a dir note),
  `.github/workflows/{test,lint,security}.yml`, `.github/pull_request_template.md`.
- Wave 1: `tests/arch/test_module_shape.py` (new),
  `tests/arch/test_layer_contract.py` (additive checks with debt allowlist),
  `tests/arch/modules/test_registry.py` (owner pins), `Makefile` (`check` += `sec`),
  `.pre-commit-config.yaml` (ruff mirror).
- Wave 2: `voiceai/modules/wallet/{constants,exceptions,helpers,utils,static_methods}.py`
  (new), `wallet/{__init__,errors,service,controller}.py` (wire only),
  `voiceai/modules/*/ {CONTRACT,README,RUNBOOK}.md`.

## Data model

None. No collections added. `ModuleDef` gains metadata fields only
(`owner_squad`, `slack_channel`, `runbook_path`, all defaulted — existing
`ModuleDef(name=…, router=…)` construction keeps working).

## Security notes

- CI parity closes the "green locally, red in prod" gap: `bandit` medium+/high on
  `ARCH_DIRS`, same scope locally and in `security.yml`.
- No secrets introduced. Generator emits no credentials; `.env.sample` unchanged.
- Re-enabled `pull_request` triggers use `contents: read` + pinned actions only.

## Test plan

- `tests/arch/test_module_shape.py`: template presence (`CONTRACT/README/RUNBOOK`,
  canonical 10-file set with `models/`-dir allowance), budget checks scoped to
  canonical top-level files (legacy provider/session subtrees allowlisted with
  `TODO(spec-0000)`), no `print`, no `os.getenv` outside
  `core/environment.py` + `*/adapters/` + tests.
- Extended `test_layer_contract.py`: 4 additive checks (controller/service/
  repository/common isolation + general cross-module surface) with an explicit
  `KNOWN_DEBT` allowlist so the current tree stays green and only *new*
  violations fail.
- `test_registry.py`: every `ALL_MODULES` entry carries a non-empty `owner_squad`
  and `runbook_path`.
- Wallet unit/controller suites keep passing unchanged (behavior-preserving wiring).

## Verification

```sh
make check
make sec
.venv/bin/python scripts/scaffold_module.py --help
.venv/bin/python -m pytest -q tests/arch/modules/test_registry.py tests/arch/test_module_shape.py tests/arch/test_layer_contract.py
```

## Rollout

Single merge. No flags, no migrations. Follow-ups file their own specs and use
`scripts/scaffold_module.py` from day one.

## Burn-down

- [ ] `specs/` restored (this file + README + TEMPLATE)
- [ ] Generator + docs + registry owners landed
- [ ] Shape/layer gates green, CI == make
- [ ] Wallet normalized, per-module docs landed
