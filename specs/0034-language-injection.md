# Spec 0034: language instruction injection move (M5 slice 5)

## Goal

Move `TaskManager._inject_language_instruction` verbatim to
`voice/session/language/switcher.py`, leaving a thin delegator. The method
injects the detected-language instruction into LLM messages (system_only and
per_turn modes) — language-region code beside its directive siblings, closing
out that region of TaskManager.

## Non-goals

- Any other TaskManager region (`__setup_s2s`, `__await_stream_sid`,
  `wait_for_current_message`, health reporting, S2S remainder).
- Signature or semantics changes (verbatim move, quirks preserved:
  silent passthrough when off, per-mode injection, swallowed errors).
- Deleting the delegator (TaskManager stays API-stable for legacy callers).

## Interface contracts

- `switcher.py::inject_language_instruction(session, messages)` —
  session-first, verbatim body, `otobaai.voice` logger per move discipline.
  `LANGUAGE_NAMES` already bridged (language_runtime adapter).
- `TaskManager._inject_language_instruction` becomes a one-line delegator.
- Tests: new seam tests in `voiceai/modules/voice/tests/session/`
  driving the function directly (mode off → passthrough; system_only injects
  first system message; per_turn injects all user messages; template errors
  swallow); existing suites pass unchanged through the delegator.

## Data model

None. No collections, no migrations.

## Security notes

- No payload logging added (existing identifier-only lines verbatim).
- `make sec` clean.

## Test plan

New seam tests + `make check` (language suites exercise neighbors).

## Verification

```sh
make check
make sec
```

## Rollout

Single merge, behavior-identical. Rollback is revert.

## Burn-down

- [x] Language injection → `language/switcher.py` + delegator + tests
  (voice budget 46800→46900 with ratchet-down note).
