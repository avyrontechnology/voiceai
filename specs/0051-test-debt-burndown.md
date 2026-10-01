# Spec 0051: test debt burn-down

> Owner directive (2026-10-01): "sirf verify audit hi mat karte raho, changes karo code
> me" — stop cataloguing the seven known `make test-all` failures and the five `make cov`
> deselects; fix the code where a test is right, retire the test where the code is right,
> and leave the suite with zero documented known failures.

## Goal

`make test-all` (3601 collected at HEAD 7d34c680) reports 7 pre-existing failures and
`make cov` deselects 5 tests to reach its gate, all documented as "known legacy debt" in
AGENTS.md §8. Each failure is one of four contracts that drifted between the test and the
code. This spec settles every one of them with a written decision, lands the one real
engine fix hiding among them (a half-dead telephony socket is never detected, so the
output loop keeps stalling on every packet for the rest of the call), and removes every
deselect so `make cov` and `make test-all` run the same suite.

## Non-goals

- Harmonising `handle_interruption()` across all telephony providers. Plivo, Exotel and
  Vobiz still latch closed on the FIRST timed-out interruption clear (their legacy
  `except Exception`); Twilio treats one as transient. Moving the other three to the
  transient policy is blocked on `tests/test_cleanup_downstream_survives_dead_output_socket.py`
  (outside this batch's file set — it asserts `is_closed() is True` after a single
  timed-out clear) and on `exotel.py` / `vobiz.py`. Follow-up, see Burn-down.
- Changing the prompt loader's return contract (`None` for "no stored prompts" stays;
  see Interface contracts (b)).
- Retiring `tests/test_seed_mongo_users.py` (imports a git-ignored `scripts/` file;
  `--ignore` stays).
- The S3 branch of `get_prompt_responses` (stdout `print` / `traceback.print_exc`):
  legacy engine, migration source, untouched.
- Editing files outside this batch's set. Four integrator items fall out of the new contract
  and are listed in Burn-down with their exact patches (`git apply`-ready, see Verification):
  the `CallOutputPort` latch docstring (`voiceai/modules/voice/ports/telephony.py:9-12,125`)
  and the voice RUNBOOK alert line still say a send timeout never latches; the
  `OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS` literal belongs in `voiceai/modules/voice/constants.py`
  (rule 1b); Exotel/Vobiz still clear the mark ledger only after a successful send.

## Interface contracts

### (a) `tests/test_clinic_appointment_agent.py` — RETIRED (file deleted)

Imports `examples.clinic_appointment_booking_agent`, deleted in `2c385eff` ("fixed db
issue") together with every other file under `examples/` (`simple_assistant.py`,
`text_only_assistant.py`). The example was a runnable demo of a Sarvam + Gemini clinic
agent built on `voiceai.assistant.Assistant`; the three tests pinned that demo's own
config literals (provider names, `shubh` voice casing, the `book_appointment` tool
schema), not library behaviour. Nothing in `voiceai/` imports it and the example tree is
gone, so the tests pin a dead artifact. Decision: delete the test. The file is removed
from the working tree with the filesystem (no `git rm` in this batch); the integrator
stages the deletion.

### (b) `tests/test_prompt_resilience.py` — test was stale; loader contract pinned as-is

`voiceai.helpers.utils.get_prompt_responses(assistant_id, local=True)` answers
**`None`** when `agent_data/<id>/conversation_details.json` is missing or unreadable
(and on any S3 failure), never raises, and answers exactly the stored payload otherwise.
The two failing tests asserted `{}`. Every consumer already handles `None`:

| Call site | Handling |
|---|---|
| `voiceai/modules/voice/session/prompts.py:229-237` (engine, `load_prompt`) | `if not isinstance(prompt_responses, dict): … = {}` — pinned by `voiceai/modules/voice/tests/session/test_prompts.py::test_non_dict_payload_degrades_to_an_empty_system_prompt` |
| `voiceai/modules/agents/utils.py::read_conversation_details` | typed `dict[str, Any] \| None`, docstring: "a missing or unreadable file answers `None` (callers degrade to empty prompts — never an error)" |
| `voiceai/agent_manager/task_manager.py` | imports the name only for the lookup-site bridge; the body lives in `session/prompts.py` |

`None` is a meaningful signal ("no stored prompts" vs. a stored `{}`) that the agents
module documents and types, so it stays. Changes:

- `voiceai/helpers/utils.py::get_prompt_responses` (local branch only): the comment and
  log line claimed the loader returns empty prompts ("using empty prompts", "Never return
  a non-dict sentinel") while returning `None`. Both now state the real contract.
  Behaviour unchanged.
- `tests/test_prompt_resilience.py` rewritten to pin: missing file → `None` without
  raising; unreadable (non-JSON) file → `None` without raising; readable file → the exact
  stored dict; the agents-module reader answers `None` for a missing agent; and the engine
  seam (`load_prompt`) degrades a `None` payload to an empty system prompt (reference to
  the module test above — not duplicated).

### (c) Telephony output: bounded dead-socket detection (real bug, fixed)

Files: `voiceai/modules/voice/io/output/telephony.py` (base),
`…/telephony_providers/twilio.py`, `…/telephony_providers/plivo.py`,
`tests/test_telephony_output_send_timeout.py`.

Commit `233651ca` made a timed-out send transient (packet dropped, handler stays open)
because latching on the first 5 s timeout muted the agent for the rest of the call on
throttled hosts. That left the opposite hole the deleted test asserted against: a
half-dead socket (TCP stalls, no close frame — `send_text()` never returns) is **never**
detected, so every later packet stalls for the full `OUTPUT_SEND_TIMEOUT_S`, the output
loop pours audio into the void until the carrier's idle-stream detection ends the call,
and `is_closed()` never turns true. Both tests were right about the symptom and wrong
about the bound: one timeout is not proof of death; N in a row is.

New contract on `TelephonyOutputHandler` (public surface):

- `OUTPUT_SEND_TIMEOUT_S` (unchanged, module constant, lookup site for the dead-socket
  suites): bound on every `_send_text`.
- `OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS: Final[int] = 3`: the number of **consecutive**
  timed-out sends after which the socket is presumed dead and the handler latches
  `is_closed() == True`. A successful send resets the streak to 0. Its home is
  `voiceai/modules/voice/constants.py` (rule 1b: every voice-module literal), imported by
  `telephony.py`, which keeps the name bound in its own globals so the tests'
  `monkeypatch.setattr(telephony_module, …)` keeps working and `_send_text` reads it at call
  time. It is defined in `telephony.py` in this batch only because `constants.py` is outside
  the batch's set; the integrator's line-neutral move is in Burn-down.
  Worst-case detection latency is `OUTPUT_SEND_TIMEOUT_S × OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS`
  (15 s at defaults) instead of never. Setting it to 1 restores the pre-`233651ca`
  latch-on-first policy.
- `_send_text(message)`: still raises `asyncio.TimeoutError` on a timed-out send (callers
  keep dropping the packet and logging); as a side effect it maintains the streak
  (`_timeout_streak`) and latches the handler when the bound is reached. Once latched,
  `handle()` drops packets without touching the socket (existing early return).
  The voice module had 14 lines of headroom under its `max_lines` budget
  (`tests/arch/test_size_budgets.py`); this change is deliberately compact (+14 net) and
  lands the module exactly at the budget — the next voice-module addition needs a budget
  bump in `voiceai/modules/voice/__init__.py` (`max_lines`) in its own spec.
- `reopen()` (unchanged, `DefaultOutputHandler`) clears the latch but not the streak, so a
  reopened handler over a still-dead socket re-latches on its very next timeout — exactly
  the "next send fails immediately and re-latches" promise in its docstring. A successful
  send after reopen resets the streak.
- Twilio `handle_interruption()`: unchanged policy (one timeout is transient); its log
  line now reports the actual latch state instead of asserting "keeping socket open". The
  redundant try-body `clear_data()` is gone — its guarded `finally` already runs the clear,
  so a healthy barge-in clears the ledger once, not twice (one `VOICEAI_TRACE_MARK clear`
  line per barge-in instead of a second one reporting `pending=0`).
- Plivo `handle_interruption()`: unchanged latch policy (see Non-goals); the mark-ledger
  `clear_data()` now runs in a **guarded** `finally` AFTER the send attempt — exactly
  Twilio's block (`try: clear_data() / except Exception: logger.warning(...)`), so:
  - the ledger is cleared even when the send stalls or fails — stale marks plus a latched
    socket is a double fault (the playback oracle keeps reporting audio in flight for a leg
    that will never ACK);
  - a fault inside `clear_data()` is logged and swallowed, never raised out of
    `handle_interruption()`: `__cleanup_downstream_tasks` (`history_sync.py`) and
    `_s2s_drop_queued_audio` (`s2s_runner.py`) await it bare, so an escaping exception
    would abort the whole barge-in cleanup. Pinned by
    `test_interruption_survives_a_faulting_mark_ledger` (Plivo + Twilio).
  It must stay after the send, never before it: `asyncio.wait_for` yields before the
  wrapped send starts and `__cleanup_downstream_tasks` cancels the output loop only after
  `handle_interruption()` returns, so a packet the output loop pushes in that gap lands
  marks that the carrier's clear frame then discards; a clear-before-send would leave those
  marks pending and hold `has_pending_marks` (hangup / cleanup gates) true. Pinned by
  `test_interruption_clears_marks_landed_while_the_clear_frame_was_in_flight` (all four
  providers — Exotel/Vobiz already clear after a successful send).
- Line budget: the guarded `finally` costs Plivo +2 lines; paid in-set by collapsing the
  constant's comment in `telephony.py` (-1) and dropping Twilio's redundant clear (-1), so
  the voice module stays exactly at its `max_lines` (net 0 on top of the +14 above).

Existing pins outside this batch that the new contract keeps green:
`tests/test_characterization_default_io.py::test_telephony_send_timeout_drops_packet_but_keeps_socket_open`
(one timeout on `handle()` does not latch),
`tests/test_cleanup_downstream_survives_dead_output_socket.py` (Plivo's timed-out
interruption clear latches; cleanup returns within bounds),
`voiceai/modules/voice/tests/io/test_relocation.py::test_send_timeout_lookup_site_is_the_new_telephony_module`.

### (d) `tests/arch/common/test_constants.py::TestAppVersion` — deselect removed, test made env-robust

The test passes in the repo venv (`importlib.metadata.version("voiceai") == "0.10.217"`).
It was deselected as "the suite-baseline env failure": on a raw checkout that was never
`pip install -e`'d, `importlib.metadata.version` raises `PackageNotFoundError` while
`APP_VERSION` resolves to the `_FALLBACK_VERSION` sentinel. The test now asserts the
constant against whichever of the two the environment yields — a real pin in both
environments, no skip, no deselect.

### Makefile / AGENTS.md

- `Makefile` `cov` target: all five `--deselect` lines removed; the comment above it no
  longer refers to known failures. `--ignore=tests/test_seed_mongo_users.py` stays.
- `AGENTS.md` §8 first bullet: the enumerated suite failures ("telephony send-timeout
  contract, prompt-loader contract, prompts-endpoint auth") are gone; the bullet keeps
  only the `scripts/`-import note.

## Data model

N/A — no persisted models; the only new state is the per-handler in-memory
`_timeout_streak` counter.

## Security notes

- No new env reads (the new bound is a module constant; `OUTPUT_SEND_TIMEOUT_S` keeps its
  existing `os.getenv` from spec 0004 verbatim).
- Logs carry the provider name, the streak count and the latch state — never audio
  payloads, stream ids beyond what the existing trace lines already emit, or exception
  text from a client-facing path.
- No network, no secrets, no PII. Tests are offline (fake sockets, `tmp_path` prompt dir).

## Test plan

- `tests/test_telephony_output_send_timeout.py` (rewritten):
  - every provider's `handle_interruption()` returns within bounds on a hanging socket;
    per-provider first-timeout latch expectation pinned explicitly (Twilio open,
    Plivo/Exotel/Vobiz closed — the documented divergence);
  - `handle()` on a hanging socket returns within bounds and stays open after ONE
    timeout (matches the characterization pin);
  - `OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS` timed-out packets in a row latch the handler
    closed and the next packet never touches the socket (Plivo and Twilio);
  - a successful send between stalls resets the streak (no latch);
  - Twilio: one timed-out interruption clear stays open, N in a row latch;
  - `reopen()` over a still-dead socket re-latches on the next timeout;
  - a timed-out interruption clear still clears the mark ledger: Plivo + Twilio pass;
    Exotel + Vobiz are `xfail(strict=True)` (they clear only after a successful send) so
    the follow-up that guards them must drop the marks and the pin then covers all four;
  - Plivo + Twilio: a ledger whose `clear_data()` raises never makes `handle_interruption()`
    raise; the clear frame still goes out and the handler stays open;
  - all four providers: a mark landed while the clear frame was in flight is wiped (the
    ledger clear runs after the send attempt, not before it);
  - healthy-socket sends unchanged (1 clear frame; pre-mark + media + post-mark).
- `tests/test_prompt_resilience.py` (rewritten): the four loader contract pins in (b).
- `tests/arch/common/test_constants.py`: env-robust version pin.
- Regression: `tests/test_characterization_default_io.py`,
  `tests/test_cleanup_downstream_survives_dead_output_socket.py`,
  `voiceai/modules/voice/tests/io/test_relocation.py`,
  `voiceai/modules/voice/tests/session/test_prompts.py`.

## Verification

```sh
.venv/bin/python -m pytest -q tests/test_telephony_output_send_timeout.py tests/test_prompt_resilience.py tests/arch/common/test_constants.py \
  tests/test_characterization_default_io.py tests/test_cleanup_downstream_survives_dead_output_socket.py \
  voiceai/modules/voice/tests/io/test_relocation.py voiceai/modules/voice/tests/session/test_prompts.py
make lint-arch
make type
make sec
make test-all      # expected: 0 failed
make cov           # expected: no deselects, gate ≥ 85 %
```

Integrator items (files outside this batch's set) ship as one `git apply`-ready patch
produced with the batch (`spec-0051-integrator.patch`, path in the batch report); after
applying it, drop the two `xfail` params in
`tests/test_telephony_output_send_timeout.py::test_interruption_on_a_dead_socket_still_clears_the_mark_ledger`
(strict xfail turns them into failures until then — by design) and re-run the block above.

## Rollout

No flag. `OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS = 3` is a module constant; an operator who
needs the old latch-on-first behaviour on a specific deployment sets it to 1 in code
(there is deliberately no env read). Rollback is reverting the base handler change; the
tests in this spec are the contract either way. No shims, no migrations.

## Burn-down

- [x] (a) `tests/test_clinic_appointment_agent.py` deleted (integrator stages the deletion).
- [x] (b) `get_prompt_responses` comment/log tell the truth; `tests/test_prompt_resilience.py` pins the `None` contract.
- [x] (c) `TelephonyOutputHandler` bounded dead-socket latch (`OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS`), Twilio log state, Plivo interruption bookkeeping in a guarded `finally` after the send attempt (Twilio parity; ledger faults never escape), Twilio's duplicate clear dropped; `tests/test_telephony_output_send_timeout.py` rewritten; voice module stays within its size budget.
- [x] (c) review fixes: `get_prompt_responses` missing-file log at WARNING (a handled non-error path); Plivo `finally` guarded; faulting-ledger + all-provider mid-flight pins.
- [x] (d) `TestAppVersion` env-robust; Makefile `cov` deselect removed.
- [x] Makefile `cov`: all deselects removed, comment updated.
- [x] AGENTS.md §8 first bullet no longer enumerates fixed failures.
- [ ] Integrator (patch ready): move `OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS` to `voiceai/modules/voice/constants.py` as `Final[int]`; `telephony.py` imports it (line-neutral).
- [ ] Integrator (patch ready): `CallOutputPort` module docstring (`ports/telephony.py:9-12`) and `is_closed()` docstring (`:125`) say a streak of timeouts latches; voice `RUNBOOK.md` alert line distinguishes `output send timed out` (one packet dropped, leg open) from `output socket presumed dead after N send timeouts` (latched).
- [ ] Integrator (patch ready): Exotel/Vobiz guarded `finally` (twilio.py parity), `max_lines` bump in `voiceai/modules/voice/__init__.py`, then drop the two strict `xfail` params in `test_interruption_on_a_dead_socket_still_clears_the_mark_ledger`.
- [ ] Follow-up (own spec): move Plivo/Exotel/Vobiz `handle_interruption()` to the transient-timeout policy (base streak decides) and re-pin `tests/test_cleanup_downstream_survives_dead_output_socket.py` on the N-timeout contract.
