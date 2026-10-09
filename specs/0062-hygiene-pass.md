# Spec 0062: hygiene pass — process cleanup + tools module consolidation

## Goal

Process hygiene (junk commits, duplicate spec numbers, missing commit-msg hook) +
tools module file consolidation (merge parallel naming structures into canonical homes).
The tools module grew organically across specs 0056–0061 and now carries two parallel
model/repository naming schemes that were never unified.

## Non-goals

- No new features or API changes
- No legacy restructuring
- No refactoring beyond file renames + import updates + hook config

## Interface contracts

Owning module `voiceai/modules/tools`, additive only. Changes:

1. **Duplicate spec fix:** `0041-ui-identity.md` → `0063-team-membership-ui.md`
   (unique number; the spec was written after 0041 but never renumbered).
2. **Model consolidation:** `vault_models.py` merges into `models.py` — five persisted
   models now live in one home (Rule 1a: `models/` package is legitimate once a module
   owns more than three models; a single `models.py` is fine for five).
3. **Repository consolidation:** `vault_repository.py` merges into `repository.py` —
   `ToolsRepository`, `LogsRepository`, and `VaultRepository` in one file.
4. **Name clarity:** `credential_store.py` (`VaultCredentialStore`) vs `vault.py`
   (`CredentialVault`) — rename `credential_store.py` to `credential_repository.py`
   (it persists vault rows; the vault itself is crypto). Update all imports.
5. **Commit-msg hook:** `.pre-commit-config.yaml` gains a `commit-msg` hook enforcing
   conventional-commit format + `spec-NNNN` reference. Install once with
   `pre-commit install --hook-type commit-msg`.
6. **Junk commit reword:** 11 consecutive `fix`/`fixed`/`fxied`/`done`/`added` commits
   reworded via `git rebase -i` (backup branch `backup/tools-execution-mcp-2026-10-09`
   exists; branch is local-only, no force-push needed).

## Data model

N/A — no new collections. Existing `Collections` registry unchanged.

## Security notes

- Commit-msg hook is local tooling only — no supply chain impact.
- archify skill (MIT, `tt-a1i/archify`) is installed via `npx skills add` — noted here
  per AGENTS.md §4 (new dependency). It generates standalone HTML diagrams; no runtime
  code execution.

## Test plan

- `make check` green after all renames (ruff + mypy + pytest).
- `make sec` green (bandit).
- Layer contract tests (`test_layer_contract.py`) pass with renamed files.
- `test_module_shape.py` / `test_no_parallel_impl.py` updated if they pin paths.

## Verification

```sh
make check
make sec
make cov
```

## Rollout

1. Rename spec file (git mv).
2. Merge `vault_models.py` → `models.py`, `vault_repository.py` → `repository.py`,
   rename `credential_store.py` → `credential_repository.py`.
3. Update all imports across `voiceai/modules/tools/` and `voiceai/core/container.py`.
4. Update `CONTRACT.md` + specs 0056–0061 file-set sections to match reality.
5. Add commit-msg hook to `.pre-commit-config.yaml`; install locally.
6. Reword junk commits via `git rebase -i b9771cb9^` (11 rewords, rest pick).
7. Regenerate any drifted archify diagrams.

Rollback = revert. All changes are additive renames + config; no behavior change.

## Burn-down

- [x] `0041-ui-identity.md` → `0063-team-membership-ui.md` (git mv)
- [x] `vault_models.py` → `models.py` (merge + re-export shim)
- [x] `vault_repository.py` → `repository.py` (merge + re-export shim)
- [x] `credential_store.py` → `credential_repository.py` (rename + import updates)
- [x] Update `core/container.py` + all service/helper/test imports
- [x] Update specs 0056–0061 file-set sections (0059 references)
- [x] Add commit-msg hook to `.pre-commit-config.yaml`
- [x] `pre-commit install --hook-type commit-msg`
- [x] Reword 11 junk commits (git filter-branch --msg-filter)
- [x] Regenerate drifted diagrams (all 5 pass at revision e91a1abb)
- [x] `make check` green (2165 passed); `make sec` clean; `make cov` 93.24% (pre-existing `test_welcome_message_cannot_be_barged` failure documented — fails at HEAD too, unrelated to this spec)
