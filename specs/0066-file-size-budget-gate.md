# Spec 0066: file size budget gate

## Goal

Enforce a hard 800-line ceiling on every source file in the new architecture.
Today 6 files exceed it (switcher 1227, s2s_runner 1005, generation 997,
history_sync 968, report 950, kalpa_synthesizer 878). A gate test prevents
new violations and tracks existing debt.

## Non-goals

- No refactoring of existing files (they carry split specs)
- No new features
- No interface changes

## Interface contracts

New test: `tests/arch/test_file_size_budget.py`

- `test_no_file_exceeds_800_lines` — every `.py` under `voiceai/modules/` and `voiceai/common/`, `voiceai/core/`, `voiceai/database/` must be ≤ 800 lines
- `test_size_debt_entries_are_accurate` — every file in `SIZE_DEBT` (from `test_size_budgets.py`) still exists and is > 800 lines
- `test_new_files_under_800` — any file added after this spec must be ≤ 800 lines (enforced by the first test)

## Data model

N/A — no persisted models.

## Security notes

N/A — no auth or secrets.

## Test plan

- `test_file_size_budget.py` — the gate itself
- Existing `test_size_budgets.py` continues to track module budgets

## Verification

```sh
make check
make sec
```

## Rollback

- Gate is additive — no existing code modified
- Rollback = revert

## Burn-down

- [x] Create `tests/arch/test_file_size_budget.py` — 800-line ceiling gate
- [x] Existing 6 violations tracked in SIZE_DEBT with owning specs
- [x] `make check` green (2166 passed, bandit clean)
