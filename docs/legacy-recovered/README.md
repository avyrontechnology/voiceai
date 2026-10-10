# Recovered legacy WIP

Files here are **not part of the build and not run by any test suite**. They are
preserved verbatim from abandoned `git stash` entries because they document real
production failures whose root cause is expensive to rediscover, and because
dropping the stashes outright would make that knowledge unrecoverable.

Each file carries a `_STALE` suffix for exactly that reason: it does not run
against current code. Do not import from it, and do not wire it into pytest.

## Contents

| File | Recovered from | What it documents |
|---|---|---|
| `talko_s2s_audio_regression_STALE.py.txt` | `stash@{4}` | The deployed Talko S2S audio-leg failure: the relay delivers raw PCM16@16k over the rescue path while the Talko leg is configured MULAW@8k, so `ulaw_to_pcm` + an 8k→16k resample fed the model garbage and Gemini's VAD never closed the turn (marks acked 0/28, `user_spoke=False`). |
| `check_redis_STALE.py.txt` | `stash@{5}` | A standalone Redis connectivity probe. |

## Why it is not restored as a test

`talko_s2s_audio_regression_STALE.py.txt` was recovered and run: **6 of its 10
tests fail against current code** (`assert None == 'pcm'`, a byte-level MULAW
mismatch, and a turn-count assertion). The surrounding engine has changed since.
Landing a failing test would break `make check`; landing a passing one would mean
rewriting it against the new engine, which is real work that belongs in a spec
rather than a cleanup commit.

## If you want it back

The behaviour it pins is still worth a spec — the encoding contract between the
Talko relay and the S2S ingest loop is a genuine interop boundary, and it is
currently guarded only by this note. Recover the file, read the module docstring
(it states the failure precisely), and re-derive the assertions against
`voiceai/input_handlers/telephony_providers/talko.py` and
`voiceai/helpers/utils.py`.

## Stashes that were dropped

The remaining stashes were verified superseded — their content is already
committed on this branch (`stash@{1}` and `stash@{3}` are duplicates of each
other, both rooted at `e05ca266`, and their `AGENTS.md` changes are present in
`HEAD`). `stash@{0}` and `stash@{2}` held telephony work abandoned 100+ commits
ago. Only the two files above were judged worth preserving.
