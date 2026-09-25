# Spec 0031: switch-language tool injection move (M5 slice 2)

## Goal

Move `TaskManager.__inject_switch_language_tool` verbatim to
`voice/session/language/switcher.py`, leaving a thin delegator. The B9b pin
(`tests/test_switch_tool_injection.py`) explicitly deferred this move "until
B13a" — B13a has landed, so the deferral is spent.

## Non-goals

- Any other TaskManager region (proactive generation, hangup leftovers,
  regen/settle, health reporting, S2S remainder — later M5 slices).
- Signature or semantics changes (verbatim move, quirks preserved).
- Deleting the delegator (TaskManager stays API-stable for legacy callers).

## Interface contracts

- `language_runtime` adapter bridges `SWITCH_LANGUAGE_TOOL_DEFINITION`
  (§3.1 bridge 1 — the only new-arch file class permitted legacy imports);
  pools already bridged there (`TranscriberPool`, `SynthesizerPool`).
- `switcher.py::inject_switch_language_tool(session)` — verbatim body,
  session-first, `otobaai.voice` logger per move discipline.
- `TaskManager.__inject_switch_language_tool` becomes a one-line delegator.
- Tests: new `voiceai/modules/voice/tests/session/test_switch_injection.py`
  drives the seam directly (fake session); legacy
  `tests/test_switch_tool_injection.py` passes unchanged through the
  delegator (proves the move).

## Data model

None. No collections, no migrations.

## Security notes

- No URL/auth handling in this cluster. `make sec` clean.

## Test plan

New seam tests (pool-gated injection, label enum enrichment, custom
description override, tools_params entry, no-pool no-op) + legacy pin suite
green + `make check`.

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Switch-tool injection → `language/switcher.py` + delegator + tests
  (bridge export for the tool definition; B9b pin passes unchanged).
