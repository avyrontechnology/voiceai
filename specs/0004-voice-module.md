# Spec 0004 — Voice module (tranche B of the agents/voice restructure)

- **Status:** in progress
- **Branch:** `revamp/arch` (base: `master`)
- **Owner:** Monazir
- **Depends on:** spec 0001; spec 0002 (AgentDefinitionPort, agents models); AGENTS.md §3.1

## Goal

Strangle the realtime call runtime out of the 9,161-line
`voiceai/agent_manager/task_manager.py` into `voiceai/modules/voice`: ports-and-adapters
around transcription/synthesis/telephony/LLM/S2S, a session package for orchestration
(config, composition, turns, language, lifecycle, reports), and physical relocation of the
leaf voice files (IO handlers incl. talko, pools, all ASR/TTS providers, s2s). Hard cap 1,500
lines/file (target ≤ 800; the two files over the cap or near it get real splits: deepgram
4-way, kalpa 730+190). `TaskManager` keeps its class name and module path this entire phase —
the harness census (49 importer files, 31 name-mangled, 24 `__get__`-rebind, 9 `__new__`
harnesses, 5 getsource pins, ~55 string patches) makes rename/move the endgame spec, not this
one. End state here: a ~900-line facade (flagged > 800-target residual, under the cap).

## Non-goals

Renaming/moving `TaskManager`; wholesale conversion of Category A/B/D tests;
`voiceai/llms/` relocation; helpers audio-DSP physical move; the platform strangler
(spec 0005+); merging `revamp/resilient-core` (its own spec — see R8); fixing any preserved
quirk (each carries `# TODO(spec-NNNN)`).

## Design — target tree (estimated lines)

```
voiceai/modules/voice/
  __init__.py [50] · constants.py [140] · models.py [280] (runtime typed views: CallContext,
    TurnMeta — the four-ID-space meta_info contract typed — WsDataPacket, transcriber events,
    HangupDetail, LidDecisionRecord, LatencyReport, ComponentLatencies moved w/ shim)
  errors.py [60] (VoiceError hierarchy; Transcriber/Synthesizer/S2S errors aliased to legacy
    classes so run()'s attribution at tm:8643-8701 keeps working) · exceptions.py [40]
  service.py [150] VoiceCallService.run_call(...) — replaces the AssistantManager seam
  controller.py [120] WS /chat/v1/{agent_id} on the new app factory behind a flag (final step)
  static_methods.py [150] (tm 124-272 pure functions; same-named module-level delegators stay
    in task_manager.py so lookups/patches keep resolving)
  utils.py [100] · helpers.py [100]
  ports/{__init__[40],transcription[140],synthesis[120],telephony[180],llm[120],s2s[70]}.py
    — TranscriptionPort/PoolPort (+ the 3 lifted encapsulation leaks: current_turn_id,
    eager_eot_threshold, supports_regen_settle()); SynthesisPort/PoolPort + SequenceGatePort
    (typed replacement for the base_synthesizer task_manager_instance backref);
    CallInputPort (playback oracle, welcome state setter, heard-text ledger) / CallOutputPort
    (close/is_closed/reopen latch, timeout≠disconnect documented) / MarkLedgerPort;
    LlmPort + AgentBrainPort (+GraphBrainPort extension); S2SPort
  adapters/{transcription[120],synthesis[120],telephony[120],llm[80],s2s[50]}.py — §3.1
    allowlisted factories over the SUPPORTED_* registries
  session/config.py [500] (tm Region A 280-942 → CallConfig)
  session/composition.py [600] (tm Region D 1350-2110 → builds ports via adapters, wires
    queues, retires the tools service-locator; quirks reproduced verbatim incl. the
    InterruptionManager default-then-reconfigure double construction, tm:2080 raise-a-string,
    RAG_SERVER_URL env mutation)
  session/prompts.py [250] (Region E over AgentDefinitionPort)
  session/welcome.py [300] · session/dtmf.py [120] (tm:697 single-consumer guard) ·
  session/events.py [200]
  session/interruption.py [520] (InterruptionManager moved, shim left)
  session/turn/{transcript_listener[550],generation[700],function_calls[700],output_loop[650],
    history_sync[600]}.py (Regions P, J+N, B+I+K, R, F + staged trio)
  session/language/{switcher[700],lid_gate[450],handoff[400]}.py (Region Q)
  session/lifecycle/{hangup[600],report[600]}.py (Regions K/S/G-part, V)
  session/health.py [180] (Region O provider-health shadow)
  session/s2s_runner.py [700] (Region U, 20 _s2s_* methods behind a narrow facade)
  s2s/{base[120],events[130],providers/openai_realtime[450],providers/gemini_live[450]}.py
  io/mark_ledger.py [320] · io/observables.py [60]
  io/input/** and io/output/** — default/telephony bases + all providers INCLUDING talko,
    shims at every old path
  asr/base.py [140] · asr/pool.py [700] (public names FROZEN — conftest spec= mocks) ·
  asr/providers/deepgram/{connection[250],nova_session[450],flux_session[450],transcriber[250]}
    (4-way split behind the unchanged DeepgramTranscriber facade, golden fixtures first) ·
  asr/providers/{assemblyai,azure,elevenlabs,gemini,gladia,google,pixa,sarvam,smallest,soniox}.py
  tts/base.py [240] (injected SequenceGatePort) · tts/pool.py [230] · tts/stream.py [400] ·
  tts/providers/kalpa.py [730] + kalpa_http.py [190] · tts/providers/*.py (rest moved as-is)
  registry.py [190] (providers.py SUPPORTED_* maps; providers.py → star shim)
  (no repository.py — the runtime owns no persistence; a placeholder is noise per rule 1)
tests/arch/modules/voice/ mirrors the tree; test_ports conformance suites
```

**Stays legacy this phase:** `task_manager.py` (frozen path/name; end-state ~900-line
facade: legacy-signature `__init__` + `from_components()` classmethod, run() coordinator,
same-named delegator stubs for every still-pinned private); `assistant_manager.py`/
`base_manager.py` (shims only after characterization tests exist); `voiceai/llms/`;
`voiceai/helpers/` (delegating wrappers; monkeypatch targets bind to module attributes);
`voiceai/platform/` minus agent_records; `voiceai/enums.py` (shared name registry);
`local_setup/quickstart_server.py` (deployed entry; routes/shapes never change);
`voicemail_handler.py` (tm-backref behind a narrow facade protocol; absorbed at endgame).
Every moved-from path becomes a `# legacy-shim(spec-0004)` on the burn-down list.

## Behavior-invariant checklist (normative; regression test lands in B1 for any entry lacking one)

buffered_output_queue replace-to-flush — extracted loops read the queue through its owner
each iteration, never capture at construction · class-level `lid_playback_gate` default
(tm:274-278) · single-consumer guards on llm/dtmf queues (tm:697, `_is_browser_leg`) ·
unconditional `revalidate_sequence_id` in kickoff (tm:4807) · `b"\x00"` BLOCK passthrough
(tm:7410) + retired-final-chunk reset (tm:7074) · output-handler timeout≠disconnect latch ·
InterruptionManager default-then-reconfigure (tm:634 vs 836) · teardown ref-nulling
(tm:9134-9146) · llm-cancel-first teardown ordering (tm:8766-8799) · kwargs contract incl.
`task_manager_instance` · RAG_SERVER_URL env side-channel · sequence_id=-1 S2S
unconditional-send.

## Migration steps (universal gate as defined in spec 0002; one commit per step)

- **B0 — Voice ports + skeletons (pure addition).** This spec finalized; `__init__`,
  `constants`, `models`, `errors`, `exceptions`, `utils`, `helpers`, `ports/*`;
  port-conformance tests against TranscriberPool, SynthesizerPool, DefaultInputHandler,
  TelephonyOutputHandler, MarkEventMetaData, BaseS2SProvider (import-only). Includes the
  file-overlap map against `revamp/resilient-core`'s diff (R8).
- **B1 — Characterization safety net (tests only; nothing moves before it has a net).**
  assistant_manager fan-out (currently ZERO tests); real-`__init__` construction matrix
  (simple/graph/knowledgebase/multiagent/s2s) extending the lone pin at
  test_llm_verbosity_passthrough.py:65 — queue topology, tools dict, InterruptionManager
  double-construction, end_call injection, welcome preload, kwargs contract; base
  transcriber/synthesizer contract tests; default IO mark-ack flow + playback oracle +
  timeout-vs-disconnect latch; deepgram golden recorded-message fixtures (nova/flux/HTTP);
  invariant regression tests for every checklist entry lacking one. Count grows ~40.
- **B2 — Additive pre-work inside legacy (tiny in-place edits, NO moves).** Pool gains
  current_turn_id / eager_eot_threshold / supports_regen_settle() (tm call sites 5198-5274,
  4818 switch); BaseSynthesizer gains optional sequence_gate kwarg preferred over the backref;
  input handlers gain set_welcome_message_played() (tm's 4 direct writes switch). Additive
  only — spec= mocks tolerate gained surface. Gate runs the 21 pool/synth/handler files
  individually.
- **B3 — Pure-function moves + adapters + registry.** static_methods (delegators stay),
  adapters/*, registry.py with providers.py star shim (test_provider_registry_parity names
  preserved), ComponentLatencies move + shim.
- **B4 — Config parser + VoiceCallService seam swap.** session/config.py proven against the
  B1 matrix, then `__init__` consumes it while ASSIGNING THE SAME instance attribute names
  (the 9 `__new__` harnesses hand-set them); quickstart WS handler resolves VoiceCallService
  (pure delegation to AssistantManager→TaskManager inside); record_engine_execution kept.
- **B5 — S2S extraction (safest seam, ~680 tm lines).** session/s2s_runner.py behind the
  narrow facade; tm keeps same-named `_s2s_*` delegators; s2s providers → modules/voice/s2s
  with `voiceai/s2s/__init__.py` shim. SAME COMMIT: rewrite tests/test_s2s_task_manager.py
  (62 tests — string patches + `__new__` attr list); reconciliation table proves the 62
  re-land.
- **B6 — Report builders + runtime prompts (run() NOT touched).** lifecycle/report.py as pure
  builders over a teardown snapshot — run()'s finally CALLS them but the pinned goodbye-drain
  block (test_hangup_goodbye_drain_on_teardown.py:156, two ordered substrings incl. 24-space
  indent) is NOT reflowed (A0 meta-test enforces); session/prompts.py over
  AgentDefinitionPort.
- **B7 — CallLifecycle + health shadow.** lifecycle/hangup.py + session/health.py; tm keeps
  delegators for `__check_for_completion` (2 mangled pins), process_call_hangup,
  `__process_end_of_conversation`; flag groups A+D move behind the object with property
  forwarding (Category-C harnesses that hand-set hangup_triggered/_end_call_in_progress keep
  working). Gate names test_check_completion_* ×3, test_end_call_teardown_self_cancel,
  test_hangup_goodbye_drain_on_teardown (untouched-by-construction).
- **B8 — Welcome, DTMF, proactive events.** Same-named tm delegators throughout.
- **B9 — Language subsystem (honest payload math: the language_switch_tm fixture funnels
  exactly 3 files; ~11 more touch language mangled names).** B9a: the three language files
  move; the conftest fixture is ported to build LanguageSwitchCoordinator with fakes IN THE
  SAME COMMIT (+ the 3 fixture files + the rebind tests of the 5 real private bodies it
  rebinds); tm keeps the class attribute lid_playback_gate + delegating property + same-named
  private delegators for every pinned mangled name. B9b: the remaining ~11 files migrate
  file-by-file, each delegator deleted only in the commit that ports its tests;
  test_substance_gate_foreign_max.py:119 (whole-class getsource) → behavior test; ported
  rebind tests assert CONCRETE VALUES, never truthiness.
- **B10 — History/interruption commit path.** turn/history_sync.py + interruption.py move
  (shim). SAME COMMIT: test_task_manager_interruption_chain.py:180-197 getsource → call-order
  behavior tests; remaining `__cleanup_downstream_tasks` pins (19 hits) migrated;
  test_speculation_commit_logging / test_browser_leg_transcripts patch paths repointed.
- **B11 — Turn core, four gated sub-steps.** B11a function_calls (+ test_pre_call_webhook
  getsource → call-order test; convert_to_request_log patches whose LOOKUP SITE moves →
  new path; test_end_call_bargein_guard.py:106 → behavior test). B11b generation.
  B11c output_loop (B1's invariant tests precede by construction). B11d transcript_listener
  (+ the last _listen_transcriber getsource pin → behavior test; A0 meta-test list updated).
- **B12 — Physical relocation of the leaf files (now mechanical: every contract is a proven
  port).** B12a io/** incl. talko (11 handler test files named in the gate). B12b tts/**
  (kalpa split; 4 kalpa + 1 sarvam patch repoints; 12 provider files + pool trio + socket
  guard). B12c asr/** (deepgram 4-way split validated against the B1 golden fixtures;
  test_deepgram_flux + test_deepgram_turn_finalization + test_flux_stuck_turn named).
  B12d shim-inventory checkpoint: burn-down reconciled mid-tranche.
- **B13 — Composition root + run() + retirements (three gated commits).** B13a
  session/composition.py replaces Region D; register() wires adapters + AgentDefinitionPort
  into the container; `__init__` KEEPS the legacy dict signature (test_llm_verbosity_passthrough
  passes UNMODIFIED) and gains from_components(); tools dict retired internally; make cov ≥85%
  on new packages. B13b: the ONLY step that edits run()'s body — teardown/report residue
  delegates out; llm-cancel-first ordering preserved; SAME COMMIT rewrites the goodbye-drain
  getsource pin as a behavior test and retires it from the meta-test; end-state
  task_manager.py measured (~900-line facade, flagged residual, justified). B13c retirements,
  one concern per commit: (i) task_manager_instance backref dropped after grep-proof;
  (ii) dead attrs deleted (synthesizer_queue, should_respond, last_response_time,
  allow_extra_sleep, consider_next_transcript_after, started_transmitting_audio,
  llm_response_generated, first_message_passing_time, yield_chunks) — each grep-verified
  unread by any `__new__` harness first.
- **B14 — Closeout + shim audit.** controller.py WS behind a flag; burn-down audit (every
  shim lists remaining importers or is deleted); an arch test asserts no string patch targets
  a retired namespace; a line-count script proves no file > 1,500 and lists every > 800
  residual (task_manager.py ~900; nothing in the new modules). Final: make check + test-all
  (zero net-new, count ≥ baseline, reconciliation) + sec + cov.

## Security notes

No new external surface until B14's flagged WS controller (which reuses the spec-0001
envelope/auth posture and stays dark until cutover). The socket-block guard (A0) protects
every patch-path rewrite in B5/B10/B11/B12. Preserved quirks that are security-relevant
(output-handler latch, RAG_SERVER_URL env write) are documented debt owned by
`revamp/resilient-core` — never silently re-fixed here (R8).

## Test plan

B1 is the plan's heart: characterization before movement. Per-step named gates above; port
conformance suites; golden deepgram fixtures; the meta-test + socket guard from A0; coverage
≥ 85% on all new packages from B13a onward.

## Verification

Universal gate per step (spec 0002 definition); baselines re-snapshotted per step; every
rewrite carries a reconciliation table; final B14 audit results recorded here.

### Tranche B baseline (measured and recorded by B0, 2026-09-17)

- `.venv/bin/python -m pytest -q --collect-only 2>/dev/null | tail -1` →
  `2155 tests collected, 1 error in 0.61s` — the 1 error is the known
  `tests/test_seed_mongo_users.py` collection error (imports a git-ignored `scripts/`
  file; `make test-all` ignores that file, and with the same ignore flag the count is
  also `2155 tests collected`). Matches the spec 0002 A7 closing count.
- `make test-all` → `7 failed, 2148 passed, 1 skipped`. Exact failing set — the 7 known
  master failures, never fixed in this spec (real fixes live on `revamp/resilient-core`):
  - `tests/test_agent_prompts_endpoint.py::test_prompts_roundtrip`
  - `tests/test_agent_prompts_endpoint.py::test_prompts_missing_file_returns_null`
  - `tests/test_agent_prompts_endpoint.py::test_prompts_missing_agent_returns_404`
  - `tests/test_prompt_resilience.py::test_missing_prompts_file_returns_empty_dict`
  - `tests/test_prompt_resilience.py::test_missing_prompts_result_supports_get`
  - `tests/test_telephony_output_send_timeout.py::test_handle_interruption_does_not_hang_on_a_dead_socket[TwilioOutputHandler]`
  - `tests/test_telephony_output_send_timeout.py::test_handle_does_not_hang_sending_audio_on_a_dead_socket`
  The constant +1 between collected and reported outcomes is the pre-existing
  module-level skip recorded at A0.

### Resilient-core overlap map (R8; recorded by B0, 2026-09-17)

Intersection of `git diff --name-only master...revamp/resilient-core` (63 files) with
the files this spec plans to touch. Every overlapping edit here is behavior-preserving
(verbatim moves, additive members, same-named delegators), so the eventual rebase of
`revamp/resilient-core` is a mechanical path remap; steps touching these files note
line-identity or defer per R8.

| RC-diff file spec 0004 touches | Owning step(s) | R8 handling |
|---|---|---|
| `voiceai/agent_manager/task_manager.py` | B2–B13 (every extraction) | verbatim moves + same-named delegators; do-not-reformat; quirks preserved |
| `voiceai/transcriber/base_transcriber.py` | B1 (contract tests), B12c (move+shim) | additive tests first; line-identity check at move |
| `voiceai/transcriber/transcriber_pool.py` | B2 (3 lifted members), B12c (move+shim) | strictly additive in B2; line-identity check at move |
| `voiceai/synthesizer/base_synthesizer.py` | B2 (`sequence_gate` kwarg), B12b (move+shim) | strictly additive in B2; line-identity check at move |
| `voiceai/synthesizer/synthesizer_pool.py` | B2 gate, B12b (move+shim) | line-identity check at move |
| `voiceai/synthesizer/stream_synthesizer.py` | B12b (`tts/stream.py`) | line-identity check at move |
| `voiceai/input_handlers/default.py` | B2 (welcome setter), B12a (move+shim) | strictly additive in B2; line-identity check at move |
| `voiceai/input_handlers/telephony.py` | B12a (move+shim) | line-identity check at move |
| `voiceai/input_handlers/telephony_providers/{plivo,sip_trunk,vobiz}.py` | B12a (move+shim) | line-identity check at move |
| `voiceai/output_handlers/default.py` | B12a (move+shim) | line-identity check at move |
| `voiceai/output_handlers/socket_errors.py` | B12a (moves with `io/**`) | line-identity check at move |
| `voiceai/output_handlers/telephony.py` | B12a (move+shim) | line-identity check at move |
| `voiceai/output_handlers/telephony_providers/{exotel,freeswitch,plivo,sip_trunk,twilio,vobiz}.py` | B12a (move+shim) | line-identity check at move |
| `voiceai/helpers/utils.py` | B3 onward (delegating wrappers stay module attrs) | additive delegation only; audio DSP body untouched (non-goal) |
| `local_setup/quickstart_server.py` | B4 (WS handler resolves VoiceCallService) | routes/shapes/module path frozen; delegation-only edit |
| `tests/test_telephony_output_send_timeout.py` | B12a/B12b (patch repoints if lookup sites move) | KNOWN-FAILING pair preserved as failing; ported, never fixed |
| `tests/test_cleanup_downstream_survives_dead_output_socket.py` | B10 (`__cleanup_downstream_tasks` pins) | rewrite only with same-commit reconciliation |
| `tests/test_task_manager_failure_isolation.py` | B5–B13 (delegator/pin migrations) | rewrite only with same-commit reconciliation |
| `tests/test_pool_failure_isolation.py` | B2 gate (runs individually), B12b/B12c | rewrite only with same-commit reconciliation |
| `tests/test_engine_websocket_lifecycle.py` | B4 gate | rewrite only with same-commit reconciliation |
| `tests/test_output_handler_error_policy.py` | B12a gate | rewrite only with same-commit reconciliation |

RC-diff files this spec does NOT touch (no collision): `.env.sample`, `AGENTS.md`,
`README.md`, the three `local_setup/telephony_server/*_api_server.py` files,
`voiceai/agent_config.py`, `voiceai/agent_types/*` (spec 0002 shims), `voiceai/constants.py`,
`voiceai/errors.py`, `voiceai/exceptions.py` (imported for aliasing in B3 adapters, never
edited), `voiceai/helpers/resilience.py` (RC-only), `voiceai/llms/*` (non-goal),
`voiceai/models.py` (R6 endgame), `voiceai/output_handlers/telephony_providers/` none
beyond the six above, `voiceai/platform/*` (spec 0005+), `voiceai/responses.py`, and the
remaining RC test files (`test_agent_config_validation`, `test_agent_prompts_endpoint`
(known-failing, untouched), `test_carrier_auth`, `test_errors_and_responses`,
`test_llm_safe_error_message`, `test_resilience`, `test_seed_mongo_users` (ignored),
`test_stream_token`, `test_tool_argument_guard`).

B0: check=green; test-all=7/2178/2185 (net-new: 0; reconciliation: spec 0002 A1's
`test_registry_lists_exactly_the_registered_modules` in tests/arch/modules/test_registry.py
rewritten in place to admit `voice.MODULE` as the third `ALL_MODULES` entry — test count
unchanged, the A1-precedent registry edit; +30 new voice-module tests in
tests/arch/modules/voice/test_ports.py: mypy typed-assignment pins + runtime_checkable
isinstance conformance for the six B0-named legacy classes (TranscriberPool,
SynthesizerPool, DefaultInputHandler, TelephonyOutputHandler, MarkEventMetaData,
BaseS2SProvider via a no-op subclass — all built offline around in-memory fakes, no
instantiation of network things), the TaskManager issubclass pin for SequenceGatePort,
fake-only conformance for the B2-target ports (ActiveTranscriberProbePort incl. an
honest not-yet-conformant pool pin, WelcomeStateSetterPort) and the brain ports, the
module-def suite (empty router, no-op register), legacy-literal constants pins, and
typed-view pins against the real builders (build_lid_decision_record field-set equality,
create_ws_data_packet). make sec clean; make cov 98.61% (≥ 85%))

B1: check=green; test-all=7/2272/2279 (net-new: 0; reconciliation: no existing test
removed or rewritten — purely additive: +94 characterization tests in 6 new legacy-tree
files. test_characterization_assistant_manager.py ×10: the fan-out net (welcome
substitution incl. the web-call no-substitution quirk, per-task TaskManager
construction/load_prompt/run contract, run_id override, deepcopied yields,
task-0-output-as-input_parameters identity, extraction_details injection).
test_characterization_task_manager_construction.py ×19: the real-`__init__` matrix over
simple/graph/knowledgebase/multiagent/s2s with offline providers, extending the
test_llm_verbosity_passthrough.py:65 pin — queue topology, tools dict,
InterruptionManager default-then-reconfigure (tm:634/836), end_call injection
(global-primary + graph node-scoped), welcome preload + web upsample, kwargs contract
incl. the task_manager_instance backref and process_interim_results, the
RAG_SERVER_URL env side-channel, the tm:697 dtmf single-consumer guard (s2s
suppression), and the _is_browser_leg llm-queue guard predicate.
test_characterization_base_contracts.py ×22: BaseTranscriber/BaseSynthesizer contracts
incl. asr_turn_id publication, the (sequence_id, message_category) latency key, the
should_synthesize_response backref gate (SequenceGatePort's seam), chunk stamping, and
the cached HTTP fetch loop. test_characterization_default_io.py ×19: the
pre/post-mark wire protocol, the mark-ack flow, the heard-text playback oracle
(per-user/turn/response on handler and ledger), welcome/hangup mark side effects,
closed-latch + reopen, and the telephony timeout≠disconnect latch as it behaves ON
THIS BRANCH (timeout drops the packet, socket stays open; the known-failing
test_telephony_output_send_timeout pair keeps pinning resilient-core's future fix and
stays failing). test_characterization_deepgram_golden.py ×10 over 3 committed recorded
fixtures in tests/fixtures/deepgram/: nova receiver (speech_final + UtteranceEnd
fallback, user_stop stamps, turn_latencies), flux receiver (eager/resumed/confirmed/
empty-EndOfTurn speculation-cancel, punctuation rstrip, ASR-native LID events), and
the prerecorded HTTP parser. test_characterization_output_loop_invariants.py ×14:
invariant nets driving the REAL rebound methods — b"\x00" BLOCK passthrough tm:7410,
BLOCK end-of-stream flag release, SEND-path settlement, hangup gate bypass,
stuck-gate release (+ fresh-speech negative), buffered_output_queue replace-to-flush
incl. the read-through-owner mid-message swap, retired-final-chunk reset tm:7074
(+ mid-stream negative), and the unconditional revalidate-in-kickoff tm:4807 (+ the
cancel-path double revalidate). No production code touched; make sec clean.)

B2: check=green; test-all=7/2280/2287 (net-new: 0; reconciliation: 2 tests rewritten
in place, 1↔1 each, +8 additive. Rewrites: tests/arch/modules/voice/test_ports.py's
honest B0 pin test_transcriber_pool_does_not_yet_carry_the_probe_surface flipped to
test_transcriber_pool_carries_the_probe_surface (the B0-planned pin landing);
tests/test_s2s_task_manager.py::test_s2s_marks_the_welcome_as_played now asserts
set_welcome_message_played(True) on the input-handler mock instead of the retired
direct attribute write. Additive: +7 in test_ports.py (probe delegation ×2,
supports_regen_settle label-following, welcome-setter legacy pins ×2, sequence_gate
preference + backref fallback) and +1 in test_rule3a_gate_and_regen_settle.py
(capability path preferred on a real pool). Gate ran the 32 files matching
`grep -rl "TranscriberPool\|SynthesizerPool\|InputHandler\|OutputHandler" tests/
--include="test_*.py"` individually — all green except the preserved known-failing
test_telephony_output_send_timeout pair (spec said "21 files": the match set grew
with B1's characterization files). Deviations, made loud: only THREE direct
welcome-state writes exist in tm (1513/1577/2694) — the spec's 4th site "8032" is a
stale line number (tm:8032 is the s2s `is_dtmf_active` write; the only other welcome
literal, tm:1439, is the handler-constructor kwarg, which stays); retiring the eager
site's `active_transcriber` local also updated its second use at tm:5310 (same eager
branch; the transcription-port docstring's 5198-5310 range covers it);
regen_settle_can_fire prefers pool.supports_regen_settle() but keeps the verbatim
dig as the bare-transcriber fallback (test_rule3a's dig-shaped stubs pin it);
input_handlers/telephony.py and transcriber/base_transcriber.py owned but unedited
— TelephonyInputHandler inherits the setter from DefaultInputHandler, and the probe
surface lives on the pool per the spec. make sec clean.)

B3: check=green; test-all=7/2318/2325 (net-new: 0; reconciliation: no existing test
removed or rewritten — purely additive: +38 arch tests in 4 new files under
tests/arch/modules/voice/. The moves, all proven AST-identical (bodies + docstrings)
against HEAD during the step: the six tm 124-272 pure functions →
voiceai/modules/voice/static_methods.py with SAME-NAMED delegator bindings left in
task_manager.py (spelled `from ... import name as name` so the legacy module stays an
explicit re-exporter under mypy's no_implicit_reexport — tm remains the lookup/patch
site, and welcome_pcm_upsampled keeps its one shared lru_cache because the binding IS
the memoized object); elevenlabs_synthesizer + the nine SUPPORTED_* maps →
adapters/synthesis.py and registry.py entry-for-entry; providers.py → 62-name
`# legacy-shim(spec-0004)` star re-export of registry (surface + identity pinned by
test_registry.py; test_provider_registry_parity untouched and green);
ComponentLatencies → voice/models.py with the agent_manager/models.py pure-shim
(only mechanical change: `Optional[float]` → `float | None`). adapters/ carries the
five §3.1 factory modules (create_* over the live registry maps, unknown provider →
UnknownComponentLabelError) plus the transition error aliases the errors.py TODO named
(TranscriberError/SynthesizerError/LLMError/VoiceAIComponentError re-exported by
identity), and the package surface re-exports resample + the END_CALL_* constants so
static_methods imports no legacy itself (subprocess canary pins that no provider stack
loads). Deviations, made loud: HANDOFF_CLIP_CACHE / HANDOFF_CLIP_CACHE_MAX /
_NON_NODE_RESPONSE_CATEGORIES sit inside tm 124-272 but did NOT move — they are
process-wide mutable state and module data, not pure functions (rule 1g), and they
relocate with their owning subsystems (B8/B9); moved signatures gained type
annotations (mechanical rule-6 accommodation; bodies verbatim); registry.py's
docstring avoids the literal shim tag so the shim-purity AST scan does not misread it.
make sec clean; make cov 98.60% (≥ 85%).)

B4: check=green; test-all=7/2340/2347 (net-new: 0; reconciliation: 1 test rewritten
in place, 1↔1: tests/arch/modules/voice/test_ports.py's B0 pin
test_register_is_a_noop_until_b13a flipped to
test_register_binds_exactly_the_voice_call_service (the planned B4 binding landing —
the B2 pin-flip precedent). Additive: +22 arch tests — +15 in
tests/arch/modules/voice/session/test_config.py (CallConfig pinned FIRST against the
B1 construction-matrix fixtures on concrete values, then field-for-field parity
asserted against the REAL __init__ across simple/graph/kb/multiagent/s2s incl.
reference identity of task sub-dicts) and +7 in
tests/arch/modules/voice/test_service.py (run loop, newest-messages record selection,
finally-record on run failure, recorder-failure swallow, getattr run_id tolerance,
register singleton). The moves: tm Region A's pure parsing → session/config.py
CallConfig (frozen dataclass; expressions verbatim incl. the completion-prompt
suffix's interior whitespace, the raw end_call_primary and-chain value, the dead
textual_chat_agent branch collapsed to its constant-False result with the same
KeyError surface); __init__ consumes it assigning the SAME attribute names — kwargs
mutations (welcome pops, api_tools/assistant_id/process_interim_results writes,
end_call injections), task_id gates, env reads, both InterruptionManager
constructions and all composition stay in tm; the quickstart WS run loop + finally
record → service.py VoiceCallService.run_call (record fires before exceptions
re-raise; socket lifecycle stays in the handler); quickstart composes the voice
module on the A5 module-init container seam and resolves the service at module init,
where the old direct AssistantManager import loaded the engine. The 9
TaskManager.__new__ harness files ran individually: 108 passed. Deviations, made
loud: two NEW adapter files beyond the named five — adapters/session.py (legacy
constants/prompt/update_prompt_with_context for the parser, the B3 package-surface
precedent) and adapters/manager.py (AssistantManager factory at module scope +
record_execution with the hook import kept INSIDE the call so a broken platform
still lands as "Execution logging skipped") — created instead of editing B3-owned
adapter files; the service's dependency Protocols live in service.py, not unowned
ports/; mechanical accommodations: placeholder-less f-prefix dropped (F541), the two
identical kb/graph parse branches merged value-identically, the twin
context-substitution blocks share one helper; behavior nuances: an exception in
AssistantManager CONSTRUCTION now lands in the handler's generic except arm (it
previously escaped uncaught from outside the handler's try; record still skipped
either way), the record now fires before the disconnect arm's active_websockets
removal (log order only), end_call_nodes parse unconditionally while tm's elif gate
is unchanged, and the per-output INFO log moved to the otobaai.voice logger (rule 3;
content and PII quirk preserved with a TODO). make sec clean; make cov 98.53%
(≥ 85%).)

B5: check=green; test-all=7/2355/2362 (net-new: 0; reconciliation:
tests/test_s2s_task_manager.py rewritten in place 1↔1 — all 62 test functions (71
collected nodes) re-land under their unchanged node IDs. Its only functional edits:
the R3 patch-path repoints for the two lookups whose site moved into the runner
module (17× convert_to_request_log, 4× trigger_api →
`voiceai.modules.voice.session.s2s_runner.*`), and an order-independence guard on
the one caplog test (otobaai propagate=True for its duration — the runner logs
through the otobaai family per rule 3, whose root stops propagating once
configure_logging has run); the `__new__`-harness attr list is UNCHANGED by design
(all `_s2s_*` state stays on the session instance) and now says so in a comment.
Additive: +15 arch tests in tests/arch/modules/voice/session/test_s2s_runner.py
(the B3 test_static_methods precedent): shim identity for every voiceai/s2s path,
a delegator-per-moved-name pin + a session-injection pin on TaskManager, runner
lookup-site pins, sequence_id=-1 meta pins, format/welcome-gate/playout/encode
behavior at the new home, and the moved base turn-clock + usage-split contracts.
The moves, all proven AST-identical bodies (docstring re-indent aside) against
HEAD during the step: Region U — the tm "Speech-to-speech conversation" banner,
currently tm 7643-8327 after the B3/B4 shrink (the step's 7895-8576 was the
original file's numbering) and holding 25 methods, not the estimated 20 — →
session/s2s_runner.py as module-level functions taking the session as their first
parameter (kept named `self` so bodies stay verbatim; tm injects itself on every
delegation, §3.1 bridge 3) behind the typed `S2SSession` facade Protocol; tm keeps
a same-named thin delegator per method, so patch.object/`__new__`/self-dispatch
all keep resolving, and `_s2s_await_stream_sid` (tm:1259, outside Region U) stays
in tm untouched. voiceai/s2s/{__init__,events,base_s2s,openai_realtime_s2s,
gemini_live_s2s}.py → voiceai/modules/voice/s2s/{__init__,events,base,
providers/openai_realtime,providers/gemini_live}.py with all five old paths as
`# legacy-shim(spec-0004)` identity re-exports (test_s2s_providers untouched and
green; test_ports' base_s2s pin rides the shim). Deviations, made loud: TWO
compile-time name-mangling accommodations inside otherwise-verbatim bodies —
`self.__check_for_completion()` → `self._TaskManager__check_for_completion()`
(_run_s2s_conversation) and `self.__is_s2s()` → `self._TaskManager__is_s2s()`
(_hangup_after_goodbye) — required for correctness once the bodies left the
TaskManager class body (the patched-delegator seam is what the hangup test pins,
and it still intercepts); one NEW adapter file beyond B3's five,
adapters/s2s_runtime.py (the B4 adapters/session.py precedent), carrying the light
legacy values the runner and the moved Gemini provider bind (S2S timeouts,
convert_to_request_log, trigger_api, compute_function_pre_call_message,
calculate_audio_duration, pcm/ulaw transcoders, clean_gemini_schema);
adapters/s2s.py received exactly its scheduled B5 edit (the "retire with step B5"
bridge import now points at the new provider modules); base's abstract
receive_events re-declared generator-shaped (plain `def`, the S2SPort spelling) —
mypy rejects async-def-declared overrides of async generators, call sites
unchanged; mechanical accommodations reported: PEP 604/builtin generics, `-> None`
on the three `__init__`s, docstrings on public provider methods, `# noqa: S110` ×3
on the verbatim best-effort socket closes and `# noqa: B904` on the verbatim
connect-failure raise, otobaai loggers replacing configure_logger (log content
preserved). Preserved quirks: sequence_id=-1 unconditional-send verbatim
(meta + transcript packets, pinned twice), the os.getenv API-key fallbacks in
_build_s2s_provider (rule-4 debt, TODO(spec-0004) in the runner docstring), the
welcome-gate clock reset, and the b"\x00" end-of-stream sentinel. s2s_runner.py
lands at 891 lines (> 800 target, < 1,500 cap): ~685 verbatim Region-U lines plus
the S2SSession facade — flagged residual for the B14 line-count audit.
task_manager.py: 8,913 → 8,322 lines, only the named edits (import block + region
swap; do-not-reformat respected, run() untouched, goodbye-drain pin + A0 meta-test
green). make sec clean; make cov dipped to 84.52% mid-step (the moved engine
code's tests live in the legacy tree) and closes at 85.55% (≥ 85%) with the arch
pins; the cov gate formally returns at B13a.)

B6: check=green; test-all=7/2396/2403 (net-new: 0; reconciliation: no existing test
removed or rewritten — purely additive: +41 arch tests in 2 new files.
tests/arch/modules/voice/session/test_prompts.py ×23 (the B5 test_s2s_runner
precedent): TaskManager delegator pins for all four moved names (load_prompt + the
three mangled privates, session-injection asserted), lookup-site + identity pins for
the nine globals the prompts module now owns (R3), load_prompt behavior at the new
home (webhook early-return, non-dict degrade, exact final-prompt assembly with and
without fillers, call_sid/timezone stamping, the multiagent prompt_map incl. the
preserved system-prompt-clobber quirk pinned on the concrete empty value,
multilingual assembly, language-directive gating, knowledgebase injection), the
prefill/get_final/stop-words contracts on concrete values, and the port seam —
prompt_responses_from_store over AgentSessionStorePort + a byte-identical
port-vs-legacy-fetch load parity with the legacy fetch poisoned.
tests/arch/modules/voice/session/lifecycle/test_report.py ×18, the step's heart:
run()-parity — the REAL TaskManager.run() teardown driven on fully-seeded __new__
harnesses (a CancelledError from the harness's __is_s2s hook drops run() straight
into its finally) deep-equals build_conversation_report(snapshot_teardown(twin))
for the ASR+TTS leg and the s2s leg (conversation_time compared within tolerance),
and build_followup_report for extraction/summarization/webhook — plus concrete-value
pins on annotation/rebasing, the zero-start user_bot quirk, turn-id promotion and
uncovered-turn stamping, the latency_dict master-strip vs enriched progression, the
shared-reference quirks (rag/mark_tracking/chunk_marks by identity, messages deep
copy), the double lid-event capture, the popped detection entry, recording_url None,
and a no-stray-mutation sweep. The moves: Region E (original tm 2111-2276, currently
1875-2039: __get_final_prompt, load_prompt, __prefill_prompts, __process_stop_words)
→ session/prompts.py as module-level functions taking the session as `self` behind
the PromptSession facade Protocol, proven AST-identical against HEAD during the step
modulo the declared accommodations; tm keeps a same-named thin delegator per body
(mangled _TaskManager__* spellings keep resolving; load_prompt signature unchanged)
— tm 8,322 → 8,185 lines, exactly two hunks (import block + Region E swap), run()
untouched by construction, tests/arch/test_taskmanager_pins.py + the goodbye-drain
pin green. Region V (original tm 8763-9147, run()'s finally) is NOT moved: it is
re-expressed in session/lifecycle/report.py as pure builders (wire/annotate/append/
rebase/output/progression/promote/strip, verbatim interior expressions with self.X
spelled snap.X) over the frozen TeardownSnapshot dataclass, with snapshot_teardown
as the single capture seam mirroring Region V's read order (incl. calling the lid
snapshot TWICE, its health flush being idempotent) — run() keeps its verbatim inline
copy until B13b swaps it expression-for-expression. Deviations, made loud: the step
text's "run()'s finally CALLS them" was resolved AGAINST inserting calls in B6 — the
step title says run() NOT touched, no existing method seam inside the finally could
host the call without editing run()'s body, and B13b is defined as the only step
that edits it; equivalence is instead proven by the run()-parity suite. The spec's
"over AgentDefinitionPort" is typed over AgentSessionStorePort — spec 0002 split
definition CRUD (AgentDefinitionPort) from the prompt-payload store, and the payload
port is the store one; the seam is prompt_responses_from_store (imported via the
agents __all__, §3.1 bridge 4), which B13a feeds through load_prompt's EXISTING
prompt_responses kwarg, retiring the legacy fetch branch. One NEW adapter file
beyond B3's five, adapters/prompt_runtime.py (the B4 adapters/session.py / B5
s2s_runtime precedent), binding the nine legacy values Region E reads; prompts.py
re-exports them because it is the lookup site. Five compile-time name-mangling
accommodations inside otherwise-verbatim prompt bodies (the B5 precedent):
self.__{prefill_prompts,get_final_prompt,is_multiagent,apply_language_directive,
is_knowledgebase_agent} spelled self._TaskManager__*. Mechanical accommodations:
noqa F841 on the verbatim dead agent_type local, noqa UP032 on the verbatim
"task_{}".format, noqa E501 on the verbatim long log line, `prompts: Any` on the
multiagent local (mypy), the knowledgebase-injection statement unwrapped to one
line (value-identical), moved signatures gained annotations + Google docstrings,
otobaai loggers (content preserved — the full-prompt INFO line and the
summarized-data INFO line are preserved PII quirks carrying TODOs). tm's
now-unused legacy imports (get_prompt_responses, structure_system_prompt,
get_date_time_from_timezone, enrich_context_with_time_variables, pytz) were LEFT
in place: do-not-reformat, repo ruff does not flag F401, and they keep the old
module attributes resolvable for any downstream reader. No new shims (tm keeps
delegators; no legacy path emptied). File sizes: prompts.py 391, report.py 780,
prompt_runtime.py 84 — all under the 800 target. make sec clean; make cov 86.50%
(≥ 85%; the formal cov gate returns at B13a).)

B7: check=green; test-all=7/2443/2450 (net-new: 0; reconciliation: no existing test
removed or rewritten — purely additive: +47 arch tests in 2 new files.
tests/arch/modules/voice/session/lifecycle/test_hangup.py ×29 (the B5 test_s2s_runner
precedent): TaskManager delegator pins for all seven moved names (mangled
_TaskManager__* spellings included), session-injection pins (one mangled, one plain),
lookup-site identity pins for the five globals the hangup module now owns (R3), the
flag-group contract — LIFECYCLE_FLAG_GROUP_A+D enumerate exactly the nine forwarded
names, each is a TaskManager class property, CallLifecycle seeds the legacy __init__
defaults, hand-sets on bare __new__ instances materialize the lazy holder (and
_call_lifecycle answers the same object), reads flow back, del restores the
AttributeError semantics, and the CallLifecycle operations bind their session — and
behavior at the new home on stub sessions: the ignore-gate truth table, the
enter-hangup lock + audio-gate release + first-decision-stamp keep, process_call_hangup
(the exact agent_hangup packet incl. sequence_id=-1, the empty-goodbye / voicemail /
s2s immediate-end paths, the duplicate-guard with its decision-stamp quirk),
process_end_of_conversation (goodbye history append, web_call_timeout skip, duplicate
no-op), the completion watchdog's web-call-timeout (detail stamped AFTER teardown,
verbatim quirk) / completed-hangup / mark-grace-expiry branches (module asyncio.sleep
stubbed via the R3 lookup site), one backchanneling pass (clip fetch, updated-meta
packet, gap sleep) + the resample-rate split (8k telephony vs synth-rate web), and the
dead tree-node advance. tests/arch/modules/voice/session/test_health.py ×18: delegator
+ injection pins and report_provider_health's never-affects-the-call contract
(missing-callback/provider no-ops, blocking exact-args await, fire-and-forget
strong-ref-then-discard, raising callback swallowed), the _active_tool/_component_model
pool resolvers, report_component_health connect-once-then-process (+ the
unstamped-connection defer), and report_stream_connect (once-per-call latch,
browser-leg/no-sid guards that do NOT latch, welcome-delay-excluded latency, negative
clamp to 0). The moves, all proven AST-identical bodies against HEAD during the step:
original regions 3121-3204 (__process_end_of_conversation + the dead
__update_preprocessed_tree_node beside it), 4349-4398 (_enter_hangup_state /
_should_ignore_transcriber_input / process_call_hangup) and 7556-7757
(__check_for_completion + __check_for_backchanneling) → session/lifecycle/hangup.py
as module-level functions taking the session as `self` behind the LifecycleSession
facade Protocol; Region O (original 4977-5036: _report_provider_health, _active_tool,
_component_model, _report_component_health, _report_stream_connect) →
session/health.py behind HealthSession. tm keeps a same-named thin delegator per
moved body. Flag groups A+D: the step said to enumerate them from the spec seam map,
but no seam-map document exists in the repo — the groups were derived from the step
text's examples plus the moved regions' ownership and PINNED in constants
(LIFECYCLE_FLAG_GROUP_A = hangup_triggered, hangup_triggered_at, hangup_decision_at,
_hangup_processing, hangup_message_queued; LIFECYCLE_FLAG_GROUP_D =
conversation_ended, _end_of_conversation_in_progress, _end_call_in_progress,
ended_by_assistant). They live on CallLifecycle; TaskManager forwards each through a
forwarded_flag data property whose holder is created LAZILY
(hangup.session_lifecycle, instance __dict__ slot constants.LIFECYCLE_STATE_ATTR), so
the Category-C harnesses that hand-set hangup_triggered/_end_call_in_progress on
__new__ instances keep working and __init__'s untouched seeding lines now flow
through the setters. Deviations, made loud: hangup_detail did NOT move — it is
conditionally initialized (task_id==0 only, tm:519) and stamped across subsystems,
so moving it would silently widen the task_id!=0 AttributeError surface;
has_transfer / asked_if_user_is_still_there / hangup_mark_event_timeout stay in tm
(transfer state, watchdog scratch, tunable — not groups A/D); reading an A+D flag on
a bare __new__ instance now returns the seeded default instead of raising
AttributeError (benign widening; the del path restores the raise, and no harness
read-before-set exists); the dead __update_preprocessed_tree_node (zero call sites
repo-wide) moved because its region contains it; __check_for_backchanneling is not
strictly hangup but the 7556-7757 range covers both watchdogs; the helper predicates
the completion loop calls (_should_stall_hangup, _pipeline_busy,
compute_last_ai_audio_timestamp, _inject_and_run_llm) are OUTSIDE the named regions
and stay verbatim in tm — the three test_check_completion_* gate files pin them
unbound and pass untouched; no monkeypatch string-path rewrites were owed (the one
patched shared global, create_ws_data_packet in test_pre_call_webhook:363, targets
the __execute_function_call lookup site, which stays in tm); one NEW adapter file
beyond B3's five, adapters/lifecycle_runtime.py (the B4/B5/B6 precedent), binds
create_ws_data_packet / select_message_by_language / get_raw_audio_bytes /
wav_bytes_to_pcm, resample rides the B3 adapters package surface, and hangup.py
re-exports all five as the lookup site (R3). Six compile-time name-mangling
accommodations inside otherwise-verbatim bodies (the B5/B6 precedent):
self.__process_end_of_conversation ×2, self.__is_s2s ×2,
self.__cleanup_downstream_tasks and self.__get_updated_meta_info spelled
self._TaskManager__*. Mechanical accommodations: placeholder-less f-prefixes dropped
×5 (F541, the B4 precedent), noqa E501 ×5 on verbatim long log lines, noqa S311 on
the verbatim random.choice clip pick, noqa S110 on the verbatim health swallow, one
comment-only `# type: ignore[union-attr]` in active_tool (AST identical), moved
signatures gained annotations + Google docstrings, otobaai loggers (log content
preserved). Gate names all green: tests/test_check_completion_*.py ×3,
test_end_call_teardown_self_cancel, test_hangup_goodbye_drain_on_teardown
(untouched-by-construction; the goodbye-drain source pin and the A0 meta-test stay
green — run() untouched). task_manager.py: 8,185 → 7,891 lines, exactly five hunks
(import block + four region swaps). File sizes: hangup.py 671, health.py 151,
lifecycle_runtime.py 50 — all under the 800 target. make sec clean; make cov 86.14%
(≥ 85%; the formal cov gate returns at B13a).)

B8: check=green; test-all=7/2501/2508 (net-new: 0; reconciliation: no existing test
removed or rewritten — purely additive: +58 arch tests in 3 new files.
tests/arch/modules/voice/session/test_welcome.py ×31 (the B5 test_s2s_runner / B7
test_hangup precedent): TaskManager delegator pins for all four moved names (mangled
_TaskManager__* spellings included), session-injection pins per delegator, lookup-site
identity pins for the eight globals the welcome module now owns (R3), and behavior at
the new home on stub sessions — forced_first_message (the exact preloaded-welcome
packet incl. sequence_id=-1 and the full chunk-flag set, the request-log stamp via
the NEW lookup site, the 100ms concrete duration stamp, the should_record ledger
entry, the sip-trunk pcm→ulaw split, the no-audio mark-played short-circuit, the
empty-text-drops-even-preloaded-audio quirk, the mangled-dispatch synth fallback
seam, the stream-sid bail, the delay sleep via the module lookup site, and the
0.256s duration-failure fallback), synthesize_welcome_audio (never-raises contract,
processor preference, wav unwrap + resample via the NEW lookup sites, base64 text
decode), first_message (web-call immediate synth with its exact meta, telephony
stream-sid gate, turn-based bos/text/eos wrap, timeout → end-of-conversation via
the mangled seam, default_io no-op, blank-text history skip), and handle_init_event
(context injection across prompts/system-prompt/hangup-config/welcome, ack +
scheduling through the mangled __first_message seam, the context-failure
never-blocks-the-welcome quirk, the len==2 history-rewrite gate).
tests/arch/modules/voice/session/test_dtmf.py ×6: delegator + injection pins and the
consumer contract (dtmf_number: prefixed LLM turn with the exact base meta, per-digit
ledger stamps sharing one burst offset, one-bad-burst isolation, cancellation
tear-down). tests/arch/modules/voice/session/test_events.py ×21: delegator +
injection pins ×4, lookup-site pins ×4, wait_for_safe_point (ended/idle/missing-input
immediate answers, busy-poll timeout), listen_events (safe-point-then-generate with
the node-entry-index snap, speaking-caller deferral keeping the node silence timer,
unmatched-event silence, conversation-ended skip, the CancelledError-swallowing
quiet break pinned as a quirk, per-event error isolation), the static-node md5
synth path (concrete md5 packet, context substitution via the NEW lookup site,
empty-message silence) vs the LLM-node flag+kickoff path, and generate_proactive
(fresh-meta packet over the mangled meta seam, pipeline-busy flag, cancellation-as-
interruption incl. the flag left set for the interruption path). The moves, all
proven AST-identical bodies against HEAD during the step (a normalizing AST diff
script; only the declared accommodations differ): original tm 1518-1662 (currently
1307-1450: __forced_first_message + __synthesize_welcome_audio) and 7758-7894
(currently 7087-7218: __first_message + handle_init_event) →
session/welcome.py; 2982-3004 (currently 2625-2646: inject_digits_to_conversation)
→ session/dtmf.py; 3005-3120 (currently 2648-2762: _listen_events /
_wait_for_safe_point / _proactive_generate_for_event / _generate_proactive) →
session/events.py — each as module-level functions taking the session as `self`
behind the WelcomeSession / DtmfSession / EventSession facade Protocols; tm keeps a
same-named thin delegator per moved body (mangled _TaskManager__* spellings keep
resolving; the init_event_observable registration of handle_init_event rides the
delegator). The tm:697 single-consumer guard on the dtmf queue did NOT move — it is
constructor wiring (`dtmf_enabled and not self.__is_s2s()` at the __init__ call
site, currently tm:569-571), stays verbatim in tm until composition (B13a), is
pinned by the B1 construction matrix, and is documented in dtmf.py's docstring +
the tm delegator comment. Deviations, made loud: TWO new adapter files beyond B3's
five (the B4/B5/B6/B7 precedent) — adapters/welcome_runtime.py (the eight
helpers.utils values welcome binds; resample rides the B3 adapters package surface)
and adapters/events_runtime.py (the four helpers.utils values events binds); the
new-home function names strip the mangled/underscore prefixes
(forced_first_message, listen_events, ... — the B7 process_end_of_conversation
precedent) while every TaskManager name is unchanged. Six compile-time
name-mangling accommodations inside otherwise-verbatim bodies (the B5/B6/B7
precedent): self.__await_stream_sid, self.__synthesize_welcome_audio,
self.__process_end_of_conversation and self.__first_message in welcome, and
self.__get_updated_meta_info in dtmf and again in events, spelled
self._TaskManager__*. No monkeypatch string-path rewrites owed (R3): the only
tm-path patches over names the moved bodies also read — convert_to_request_log
(test_browser_leg_transcripts ×6, test_speculation_commit_logging ×7,
test_pre_call_webhook ×11) and create_ws_data_packet (test_pre_call_webhook:363) —
target the _handle_transcriber_output / _listen_transcriber /
__execute_function_call lookup sites, which stay in tm; none exercises the moved
paths. Mechanical accommodations: placeholder-less f-prefixes dropped ×3 (F541, the
B4 precedent: the two "Executing the first message task" lines and "Shouldn't
record"), noqa UP032+E501 on the verbatim `.format` duration-failure log, noqa E501
on the verbatim deferring-to-conversation-flow log line and the two verbatim
commented-out legacy lines in first_message's default_io branch, moved signatures
gained annotations + Google docstrings, otobaai loggers (log content preserved).
Preserved quirks carrying TODOs: handle_init_event's INFO logging of the init
payload/context/welcome text (PII, rule §4) and listen_events' traceback.print_exc
stderr write (rule 3); preserved without TODO as behavior pins: the empty-welcome-
text-drops-preloaded-audio null, the 0.256s duration fallback, the sequence_id=-1
ungated welcome/proactive sends, and the listener's CancelledError swallow. No new
shims (tm keeps delegators; no legacy path emptied). task_manager.py: 7,891 → 7,547
lines, exactly four hunks (import block + three region swaps); run() untouched by
construction, tests/arch/test_taskmanager_pins.py + the goodbye-drain pin green.
File sizes: welcome.py 429, events.py 236, dtmf.py 107, welcome_runtime.py 70,
events_runtime.py 51 — all under the 800 target. make sec clean; make cov 86.55%
(≥ 85%; the formal cov gate returns at B13a).)

B9a: check=green; test-all=7/2578/2585 (net-new: 0; reconciliation: the
language_switch_tm funnel rewritten in place 1↔1 — tests/conftest.py's fixture now
builds a LanguageSwitchCoordinator over the SAME MagicMock session double (the five
real private bodies it re-bound off TaskManager — __switch_audio_gap_s /
__switch_settle_ms / __switch_decide_timeout_s / __record_lid_event /
__detector_corroborates — are re-bound onto the double from the MOVED functions),
and its exactly-3 files re-land under unchanged paths and test names:
tests/test_language_switch_audio_gap.py ×12, tests/test_stale_decision_guard.py ×3,
tests/test_recent_turns_and_gate_lifecycle.py ×7 (the last also re-pins
recent_detected_turns / snapshot_lid_events at the new home). Additive: +77 arch
tests in 4 new files under tests/arch/modules/voice/session/language/ —
test_lid_gate.py ×20 (delegator + session-injection pins for all ten lid_gate
names, the staticmethod-identity pins for the three pure evidence readers, the
lid_playback_gate stays-a-plain-class-attribute-None pin, R3 lookup-site identity
pins, and gate/evidence/telemetry behavior on concrete values), test_switcher.py
×21 (delegator + injection pins for all eleven switcher names incl. the two public
ones, the FIVE fixture-rebound bodies pinned on CONCRETE VALUES through both the
new-home functions and the TaskManager delegators — the B9a "rebind tests", R3
lookup-site pins, directive/followup behavior, and the coordinator's construction +
delegating lid_playback_gate property), test_handoff.py ×10 (cache-identity,
delegator/injection, R3 and play/text/wire behavior) and test_behavior.py ×26 (an
arch mirror of the ported fixture driving the REAL moved bodies: run_language_switch
outcomes — switched incl. history correction + handoff + followup, timeout, stay,
unsupported, no_synth, alphanumeric veto, explicit-only both ways,
function-call-in-flight, both idle-flush history paths, speculation commit, empty
drain — the handle_language_switch lock/discard wrapper ×4, spawn + mismatch, the
real lid_idle_watcher fire/skip/speaking-deferral, the real switch_language full and
subset paths, prewarm render/cache/sentinel/inert paths, and a coordinator
passthrough sweep). Census (verified by grep, the step's honest-math instruction):
19 test files touch language mangled names; the fixture funnels exactly 3;
__run_language_switch 13 hits ✓, __prewarm_handoff_clips 11 ✓,
__buffered_language_evidence 6 hits in 5 files, __arm_lid_playback_gate 6 hits in
2 files — the remaining ~16 files (substance_gate, explicit, race, drift, tunables,
handoff_prewarm, spec_cleanup, lid_idle_watcher ×2, speculation_commit_logging,
speculative_followup_history, simple_agent_language_directive, lid_usage_tracking,
live_marker_and_pin, switch_tool_injection, characterization_output_loop_invariants)
pass UNTOUCHED through the delegators (run individually before and after the port:
166 passed) and migrate at B9b. The moves, all proven AST-identical against HEAD by
a normalizing checker (mangled spellings, docstrings, annotation-stripping, the
declared return-None accommodation): Region Q scoped as the CONTIGUOUS language
cluster, currently tm 4880-6295 minus the speculation trio —
voiceai/modules/voice/session/language/lid_gate.py (collect_flux_lid_events,
__language_switch_enabled, the playback-gate trio, the three pure evidence readers,
__detector_language_mismatch, __snapshot_lid_events, __record_lid_usage/event,
__lid_idle_watcher), switcher.py (the three tunables,
_spawn_language_switch_decision, handle_language_switch, __run_language_switch,
__prepare_followup_generation, the directive pair, __generate_switch_followup,
switch_language, + the LanguageSwitchCoordinator facade, the B7 CallLifecycle
precedent) and handoff.py (__play_switch_handoff, __handoff_text_for,
__handoff_mulaw_wire, __prewarm_handoff_clips, __handoff_clip_convert, + the
process-wide HANDOFF_CLIP_CACHE/_MAX, the rule-1g module state flagged at B3, moved
WITH its owner; task_manager.py re-binds both names by identity so
test_handoff_prewarm's import-and-clear keeps operating on the one real cache — the
B3 welcome_pcm_upsampled precedent). tm keeps a same-named thin delegator per moved
name (mangled _TaskManager__* spellings included; the three pure readers stay
class-reachable as staticmethod bindings of the MOVED function objects BY IDENTITY,
so unbound TaskManager._TaskManager__buffered_language_evidence(pool, ...) calls
keep resolving) and injects itself (the LanguageSession facade) on every call (§3.1
bridge 3). Deviations, made loud: (1) the speculation-commit trio
(__speculative_followup_text / __log_committed_speculation /
__log_discarded_speculation) did NOT move although it sits inside the contiguous
cluster — step B10 explicitly owns the tests/test_speculation_commit_logging.py
patch-path repoints (its 7 patches on voiceai.agent_manager.task_manager.
convert_to_request_log exercise exactly those bodies), and R3's same-commit-rewrite
step list names B10, not B9; the trio stays verbatim in tm and the moved bodies
reach it through the session's mangled names, so B9a owes ZERO patch-string
rewrites. (2) The step text's "tm keeps the class attribute lid_playback_gate +
delegating property" was resolved as: the class attribute stays a PLAIN None (the
checklist entry, pinned at CLASS level by tests/test_language_switch_race.py:198 —
a descriptor would break it) and the DELEGATING PROPERTY lives on
LanguageSwitchCoordinator, forwarding to the session attribute; an arch test pins
both facts. (3) The scattered language-adjacent members OUTSIDE the contiguous
cluster stay in tm with their owning regions (language property/setter,
_invalidate_response_chain, _inject_language_instruction,
__inject_switch_language_tool — setup regions, B13a; _maybe_update_tts_language —
transcriber region, B11d); the substance-gate getsource pin
(test_substance_gate_foreign_max.py:119) targets the eager CALL SITE in
_listen_transcriber, which stays, so it passes untouched until B9b rewrites it. One
NEW adapter file beyond B3's five, adapters/language_runtime.py (the B4-B8
precedent), binding the two pool classes (as PLAIN aliases so mypy keeps narrowing
the verbatim isinstance checks; retire at B12b/B12c), five helpers.utils values and
the eight language constants; each language module re-exports what it reads as its
own lookup site (R3), with trailing_utterance_text / build_lid_decision_record /
is_alphanumeric_readout imported from the B3 static_methods home and
SUPPORTED_OUTPUT_TELEPHONY_HANDLERS from the B3 registry. Thirty compile-time
name-mangling accommodations inside otherwise-verbatim bodies (the B5-B8
precedent): every self.__<name> dispatch spelled self._TaskManager__<name> (19
switcher, 6 lid_gate, 5 handoff — incl. the seams to the not-yet-moved
__cleanup_downstream_tasks, __do_llm_generation, __enqueue_chunk and the
speculation trio). Mechanical accommodations, reported: 13 bare `return` →
`return None` in run_language_switch (mypy requires the explicit value under an
Optional return annotation; value-identical), `events: list = []` in
collect_flux_lid_events, three comment-only type: ignore (one no-any-return on the
prepare seam, two union-attr on the verbatim get_active_*_info ternaries — the B7
precedent), noqa E501 ×3 (two verbatim long log lines + the recent_turns ternary
that crossed 120 chars once mangled), moved signatures AND inner defs
(detected_lang_duration, as_float, emit_lid_decision, render) gained annotations +
Google docstrings, new-home names strip the mangle prefixes (the B7/B8 precedent),
otobaai loggers (log content preserved). No new shims (tm keeps delegators; no
legacy path emptied; the burn-down list is unchanged). task_manager.py: 7,547 →
6,437 lines, exactly the named edits (import/cache-binding hunk + the two region
swaps around the kept trio); run() untouched by construction,
tests/arch/test_taskmanager_pins.py + the goodbye-drain pin green. File sizes:
lid_gate.py 519, handoff.py 276, language_runtime.py 110, __init__.py 33 — under
the 800 target; switcher.py 1,139 (> 800 target, < 1,500 cap): ~700 verbatim
Region-Q lines plus the SwitcherSession facade and the LanguageSwitchCoordinator —
flagged residual for the B14 line-count audit (the B5 s2s_runner precedent). make
sec clean; make cov dipped to 82.12% mid-step (the moved decision core's tests live
in the legacy tree) and closes at 86.31% (≥ 85%) with the arch behavior mirror; the
formal cov gate returns at B13a.)

## Risks (register for both tranches)

- **R1 name-mangled tests (31 files):** class/module frozen; same-named delegators per
  extracted private, deleted only with their ported tests; honest fixture math (3 + ~11);
  rebind tests assert concrete values (Mock truthiness passes vacuously).
- **R2 getsource pins (5 files):** single-owner map (B6 must not reflow run(); B9b, B10,
  B11a/B11d, B13b own their files); A0 meta-test; task_manager.py excluded from fmt.
- **R3 dead-namespace string patches (~55):** socket-block guard from day one; same-commit
  patch rewrites listed per step (A7, B5, B10, B11a, B12b); rewrites only where the LOOKUP
  SITE moves; B14 greps that every patched attribute exists in its named module.
- **R4 zero-test regions (34 modules; assistant_manager worst):** B1/A6 land characterization
  first; `__init__` default signature never changes (from_components is additive); cov ≥85%.
- **R5 layer-matrix vs strangler:** AGENTS.md §3.1 narrow bridges, mechanically enforced by
  tests/arch/test_layer_contract.py; transitional modules→legacy contract imports bounded to
  voiceai.llms and voiceai.enums, declared with follow-up spec references.
- **R6 models star-import web:** superset shim + dir() snapshot + engine-free canary; the
  models→providers star severed only at endgame; A5 curl smoke exercises the deployed path.
- **R7 silent agent-goes-silent invariants:** the checklist is normative; B1 regression tests
  precede owning steps; owning steps name their invariants in verification.
- **R8 resilient-core collision (45+18 files):** strictly behavior-preserving verbatim moves;
  quirks preserved; known-failing tests ported as known-failing; B0 carries the file-overlap
  map; steps touching overlapping files note line-identity or defer — the eventual rebase is
  a mechanical path remap.
- **R9 gate integrity under suite drift (1,706→1,839→1,862 measured):** A0 re-measures;
  every gate = zero NET-NEW failures vs the re-snapshotted baseline AND count ≥ baseline AND
  reconciliation table; gate results quoted in commit messages (CI is dispatch-only).
- **R10 spec= mock surface freeze:** pool/handler public surfaces FROZEN; ports codify them
  verbatim; B2 additions strictly additive; B2 gate runs the 21 files individually.
- **R11 deepgram split:** golden fixtures land at B1, long before B12c; unchanged facade;
  named gate tests; spec-documented fallback = move whole at 1,321 (under cap, flagged
  residual with its own follow-up spec).
- **R12 data-shaped external contracts:** each named in constants/port docstrings as a
  preserved quirk with `# TODO(spec-NNNN)`; quickstart module path/routes/shapes frozen;
  nothing "fixed" here.

## Rollout

Strangler: every step green and revertible; quickstart remains the deployed entry until the
flagged controller cutover (endgame spec). Endgame specs after B14: TaskManager rename/move
to session/call_session.py; Category A/B/D wholesale conversion; llms relocation; helpers
DSP move; platform strangler (spec 0005+); resilient-core merge.

Shim burn-down (`# legacy-shim(spec-0004)` files; deletions happen at cutover, audited at
B12d and B14):

- `voiceai/providers.py` (B3) — star re-export of `voiceai.modules.voice.registry`,
  which preserves the full 62-name star surface (classes, the five provider enums,
  `elevenlabs_synthesizer`, the nine `SUPPORTED_*` maps) by identity for the star-import
  consumers (task_manager.py:63, `voiceai/models.py`) and every direct importer
  (tests/arch/modules/voice/test_registry.py pins the surface and the identity).
- `voiceai/agent_manager/models.py` (B3) — pure re-export of `ComponentLatencies`,
  which lives in `voiceai.modules.voice.models`; task_manager's `from .models import
  ComponentLatencies` rides the shim unchanged.
- `voiceai/s2s/__init__.py`, `voiceai/s2s/events.py`, `voiceai/s2s/base_s2s.py`,
  `voiceai/s2s/openai_realtime_s2s.py`, `voiceai/s2s/gemini_live_s2s.py` (B5) —
  pure identity re-exports of `voiceai.modules.voice.s2s` (package surface, the
  event types, the base class incl. the reconnect constants, and each provider
  module's public surface). Remaining importers: task_manager.py:64 (`s2s_events`
  rides the events shim), tests/test_s2s_providers.py, tests/test_s2s_task_manager.py,
  tests/arch/modules/voice/test_ports.py:77 (base_s2s), tests/manual/
  s2s_audio_health.py (gemini_live_s2s), and the B5 identity pins in
  tests/arch/modules/voice/session/test_s2s_runner.py.
