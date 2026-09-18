# Spec 0004 — Voice module (tranche B of the agents/voice restructure)

- **Status:** done (steps B0-B14 landed; shim burn-down stays open until the endgame cutover spec)
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

B9b: check=green; test-all=7/2578/2585 (net-new: 0; reconciliation: no test added,
removed or renamed — the remaining language-pinning test files rewritten in place
1↔1, each re-landing under its unchanged path and test names: 135 collected nodes
across 14 files, run individually before and after the port, all green. Census
(re-grepped per the step; B9a's line said "~16 remaining"): 16 files touched
language mangled names after B9a's 3-file fixture funnel; 14 ported here, 2 left
untouched BY OWNERSHIP — tests/test_speculation_commit_logging.py (step B10 owns
the tm-resident speculation trio and its patch repoints) and
tests/test_characterization_output_loop_invariants.py (a B1 output-loop net owned
by B11c; its language touches are session-double mocks, never TaskManager
rebinds). The 14 ports, all to the coordinator seam (LanguageSwitchCoordinator /
the moved module functions, with the session double's mangled `_TaskManager__*`
dispatch bound from the SAME moved bodies — the B9a fixture pattern — so no
TaskManager delegator is pinned): test_substance_gate_foreign_max ×8,
test_language_switch_explicit ×16 (decision-core half; the LanguageSwitcher half
was already engine-free), test_language_switch_race ×13,
test_language_switch_drift ×20, test_language_switch_tunables ×11 (8 defs + 3
parametrized), test_handoff_prewarm ×21 (HANDOFF_CLIP_CACHE now imported from its
B9a home — the identity-same dict), test_handle_language_switch_spec_cleanup ×4,
test_lid_idle_watcher_spin ×3 + test_lid_idle_watcher_suppression ×5 (the real
ignore-input predicate bound from ITS B7 home,
voiceai.modules.voice.session.lifecycle.hangup, instead of the tm delegator),
test_lid_usage_tracking ×10, test_language_switch_live_marker_and_pin ×10,
test_simple_agent_language_directive ×4, test_switch_tool_injection ×4,
test_speculative_followup_history ×6. The named getsource rewrite:
test_substance_gate_foreign_max.py's whole-class inspect.getsource scan (old :119)
re-lands as an async behavior test under the SAME node id
(test_eager_call_site_passes_eager_meta_info): it drives the REAL
_listen_transcriber eager branch on a harness double wired with the REAL spawner +
mismatch + arm chain (the moved coordinator bodies) and asserts CONCRETE VALUES —
the spawner called exactly once with ("kahi tari", eager_meta_info) and the
playback gate armed under sequence_id 42 taken from eager_meta_info, while the raw
message meta deliberately carries no sequence_id (a regression to the raw meta now
fails on both asserts instead of a source-text scan). Ported rebind tests assert
concrete values throughout (gate records' outcome/sequence_id/from_language, exact
tunable floats, directive text, clip byte counts), never bare truthiness.
Deliberate residual TaskManager references, made loud:
test_language_switch_race.py keeps the CLASS-LEVEL `TaskManager.lid_playback_gate
is None` pin (a checklist behavior-invariant on the class attribute, not a
delegator); test_switch_tool_injection.py keeps `__inject_switch_language_tool`
(never moved — setup region, B13a per the B9a deviation list) while its rollout
predicate migrated to the coordinator; test_speculative_followup_history.py keeps
`__speculative_followup_text` (the B10-owned trio) while its language rebind
(`__language_directive`) migrated to the moved body. DELEGATORS DELETED: NONE —
loudly: every language delegator remains load-bearing after the port, on two
grep-verified grounds. (1) Production dispatch — tm's own remaining code calls
__language_switch_enabled (tm:725/1455), switch_language (1477 on_lid_switch bind
+ 2877/2921), __lid_idle_watcher (1489 spawn), __prewarm_handoff_clips (1603
spawn), _spawn_language_switch_decision (4165 + the 4752 eager site),
__lid_playback_gate_holds (5547, the output loop), __snapshot_lid_events +
_collect_flux_lid_events (6162-6163/6233-6234, run()'s teardown) — and the moved
bodies themselves carry 75 `_TaskManager__*` dispatches (45 switcher, 14 lid_gate,
16 handoff) that resolve through these delegators on a real call. (2) Still-pinned
names — the B9a arch suites
(tests/arch/modules/voice/session/language/test_{switcher,lid_gate,handoff}.py)
pin every delegator by name until the endgame rename spec. The step's
delete-only-with-its-ported-tests constraint is satisfied vacuously: nothing was
deleted, and after this commit the only legacy tests reaching language names
THROUGH the TaskManager class are the two B10-owned trio files. No production code
touched; task_manager.py unchanged (do-not-reformat trivially respected, run()
untouched, goodbye-drain pin + A0 meta-test green); no monkeypatch string-path
rewrites owed (no lookup site moved — R3). One gated commit (the step allows
several; with zero delegator deletions there is no per-file deletion to gate).
make sec clean.)

B10: check=green (repo ruff + strict arch lint green; mypy shows only the 5
pre-existing environment errors identical on baseline — missing types-pytz stubs
and a numpy 3.12-syntax stub the pinned 3.10 CI env never sees; bandit not
installed in either local python, exit 0 with no findings via system python);
test-all=8/2598/2606 (net-new: +21 arch tests, 2606 collected vs B9b's 2585;
+1 reported skip is the pre-existing module-level skip; reconciliation: 3 tests rewritten
in place, 1↔1 each, +0 legacy-tree tests removed. Rewrites:
tests/test_task_manager_interruption_chain.py TestCallSites — the three
inspect.getsource pins (sync_history / _handle_transcriber_output /
__cleanup_downstream_tasks) re-land under the SAME node ids as call-order
behavior tests driving the REAL moved bodies (hint-set-not-invalidate on a stub
session, cancel-without-invalidate through the delegator, new-home routing pin);
tests/test_speculation_commit_logging.py — 7 convert_to_request_log patch paths
repointed 1↔1 to voiceai.modules.voice.session.turn.history_sync (the trio's new
lookup site), binds still via the TaskManager delegators. Additive: +21 arch
tests — tests/arch/modules/voice/session/test_interruption.py ×7 (legacy-shim
identity + audio/interruption gates + sequence lifecycle + recovery stats) and
tests/arch/modules/voice/session/turn/test_history_sync.py ×14 (pure-reader
staticmethod-identity pins, delegator + session-injection pins, lookup-site pins,
evidence/trimmer/helper behavior, cleanup cancel-without-invalidate, committed /
discarded speculation logging on concrete values). The moves, all proven
AST-identical bodies against HEAD during the step modulo the declared
accommodations (InterruptionManager 35/35 methods; update_transcript identical;
sync/cleanup/trio diffs limited to added docstrings, F541 f-prefix drops and the
self→module dispatch for pure helpers): voiceai/agent_manager/
interruption_manager.py (512 lines) → voiceai/modules/voice/session/
interruption.py (545 lines, otobaai logger) with the old path as a
`# legacy-shim(spec-0004)` identity re-export; tm 1822-2296 (evidence readers,
sync_history, __cleanup_downstream_tasks), tm 1120-1142 (hint/cancel/invalidate
helpers) and tm 5008-5176 (the B9a-deferred speculation trio) →
voiceai/modules/voice/session/turn/history_sync.py (887 lines) behind the
HistorySession facade Protocol, via the new adapters/history_runtime.py bridge
(convert_to_request_log, format_messages, NON_EVIDENCE_MARK_TYPES — ChatRole /
LogComponent / LogDirection ride voiceai.enums directly, the §3.1 allowance);
tm keeps same-named thin delegators per moved name (pure readers as staticmethod
bindings BY IDENTITY, the B9a precedent, so unbound
TaskManager._get_latest_*(marks) calls keep resolving) and injects itself (§3.1
bridge 3). New-home names strip the prefixes (the B7/B8 precedent):
cleanup_downstream_tasks, log_committed/log_discarded_speculation,
speculative_followup_text, set/cancel/invalidate helpers, trim/normalized/
prepare/get_latest/has/inflight/estimate. Deviations, made loud: (1)
test_browser_leg_transcripts.py patch paths NOT repointed — its 6
convert_to_request_log patches target _handle_transcriber_output, which stays in
tm until B11d (R3: rewrite only where the LOOKUP SITE moves); (2) the 19
__cleanup_downstream_tasks pins in other files pass UNTOUCHED through the
delegators (the B9b precedent — production still dispatches
__cleanup_downstream_tasks from _handle_transcriber_output and the moved bodies
carry the mangled dispatches, so every delegator stays load-bearing; instance-attr
AsyncMock overrides keep intercepting); (3) sync_history's four
conversation_history sync_* callbacks ride lambdas over the module function
instead of self.update_transcript_for_interruption (avoids a delegator double-hop;
stubs need no extra attr); (4) history_sync.py lands at 887 lines (> 800 target,
< 1,500 cap): the commit path is one auditing unit (evidence + trim + cleanup +
trio share the HistorySession facade) — flagged residual for the B14 line-count
audit (the B5 s2s_runner / B9a switcher precedent). task_manager.py: 6,437 →
5,841 lines (import hunk + five region swaps + trio swap); run() untouched by
construction, goodbye-drain pin + A0 meta-test green; the B7 gate names
(test_check_completion_* ×3, test_end_call_teardown_self_cancel) green.
make sec clean (via system python; bandit absent from .venv); pytest-cov absent
from both local pythons so the cov gate is unmeasured this step — the formal cov
gate returns at B13a.)

B11a: check=green (repo ruff + strict arch lint green; mypy/bandit/cov carry the
B10-noted environment gaps, unchanged); test-all=8/2607/2615 (net-new: +9 arch
tests, 2615 collected vs B10's 2606; reconciliation: 2 tests rewritten in place,
1↔1 each, +0 legacy-tree tests removed. Rewrites:
tests/test_pre_call_webhook.py::test_transfer_branch_fires_before_transfer_post —
the inspect.getsource ordering pin re-lands under the SAME node id as a call-order
behavior test driving the REAL moved transfer branch (webhook-before-POST asserted
on a concrete order list); tests/test_end_call_bargein_guard.py
TestSourceGuards::test_end_call_branch_sets_in_progress_flag — the getsource pin
re-lands under the SAME node id driving the REAL end_call branch through the
delegator and asserting `_end_call_in_progress is True` (its sibling
test_listen_transcriber_uses_the_guard stays a source pin: _listen_transcriber
moves at B11d). Same-commit patch repoints (R3): _drive_transfer's three patches
(asyncio.sleep, convert_to_request_log, create_ws_data_packet) → the
function_calls lookup site; the file's other 9 convert + 9 ClientSession patches
stay at the task_manager path — they drive fire_pre_call_webhook, which stays in
tm (no lookup site moved). Additive: +9 arch tests in
tests/arch/modules/voice/session/turn/test_function_calls.py (delegator +
session-injection pins per moved name, lookup-site pins, end_call lock-out,
webhook-before-POST with history-recorded, duplicate-transfer short-circuit, and
the default-leg mock transfer's transfer_start/end event pair). The moves, proven
AST-verbatim against HEAD modulo the declared accommodations:
__execute_function_call (tm 2263-2635) + _execute_transfer_call_webhook (tm
3314-3525) → voiceai/modules/voice/session/turn/function_calls.py (759 lines, ≤
800 target) behind the FunctionCallsSession facade Protocol, via the new
adapters/function_runtime.py bridge (convert_to_request_log, format_messages,
create_ws_data_packet, update_prompt_with_context, the trigger_api trio,
END_CALL_FUNCTION_PREFIX, LANGUAGE_NAMES, TranscriberPool as a plain alias for
the isinstance narrowing — LogComponent/LogDirection/HangupReason ride
voiceai.enums directly); tm keeps same-named thin delegators (mangled
_TaskManager__execute_function_call included) and injects itself (§3.1 bridge
3). New-home names strip the prefixes (B7/B8/B10 precedent).
__store_into_history did NOT move — it is called from __do_llm_generation (tm
2987/3107), so generation (B11b) owns it. Accommodations: three mangled spellings
(self.__do_llm_generation ×3, self.__is_graph_agent, self.__play_switch_handoff),
F541 f-prefix drop, annotations + docstrings, otobaai loggers, `import aiohttp`
(the transfer POST — lint-caught, behavior-identical), noqa E501 ×3 / F841 ×2 on
verbatim lines, one `_mark_id`-style rename avoided (none owed). Preserved quirks
(R8): the end_call barge-in lock, the transfer exactly-once guard with
record-before-POST, the CALL_TRANSFER_WEBHOOK_URL env fallback (rule-4 debt,
TODO), the cancelled-transfer transfer_end in finally, the dead
set_response_prompt local. test_s2s_task_manager.py's instance-attr transfer
mocks keep intercepting (no rewrite owed — no lookup site moved for them).
task_manager.py: 5,841 → 5,267 lines; run() untouched, goodbye-drain pin + A0
meta-test green.)

B11b: check=green (repo ruff + strict arch lint green; mypy/bandit/cov carry the
B10-noted environment gaps, unchanged); test-all=8/2619/2627 (net-new: +12 arch
tests, 2627 collected vs B11a's 2615; reconciliation: no legacy-tree test added,
removed, rewritten or repointed — none owed. The suites binding generation names
(test_llm_same_turn_no_cancel's unbound
TaskManager._TaskManager__do_llm_generation(stub, ...) call,
test_llm_output_empty_eos's TaskManager._handle_llm_output(stub, ...) drives,
and the _run_llm_task instance-attr mocks) all pass UNTOUCHED through the
delegators; no suite patches convert_to_request_log at the task_manager path
while driving a generation body, so no R3 repoint was owed either. Additive: +12
arch tests in tests/arch/modules/voice/session/turn/test_generation.py
(delegator + session-injection pins per moved name, lookup-site pins, empty-final-
buffer forward, straight-to-output routing, store/commit staging, hangup-skip
without LLM touch, eager-stub stamp, request-log-then-generate). The moves, proven
AST-verbatim against HEAD modulo the declared accommodations: _handle_llm_output
(tm 2159-2202), _process_conversation_preprocessed_task (tm 2203-2236),
_process_conversation_formulaic_task (tm 2237-2263), __store_into_history (tm
2271-2319), _llm_stream_with_first_chunk_timeout (tm 2320-2356),
__do_llm_generation (tm 2357-2792), _append_eager_llm_stub (tm 2793-2811) and
_process_conversation_task (tm 2812-2938) →
voiceai/modules/voice/session/turn/generation.py (983 lines) behind the
GenerationSession facade Protocol, via the new adapters/generation_runtime.py
bridge (convert_to_request_log, format_messages, create_ws_data_packet,
compute_function_pre_call_message, is_valid_md5, LLM_FIRST_CHUNK_TIMEOUT_S, plus
VoiceAIComponentError/LLMError caught by identity — LogComponent/LogDirection/
HangupReason ride voiceai.enums directly); tm keeps same-named thin delegators
(mangled _TaskManager__store_into_history/_TaskManager__do_llm_generation
included; the async-generator _llm_stream_with_first_chunk_timeout delegator
re-yields) and injects itself (§3.1 bridge 3). New-home names strip the prefixes
(B7/B8/B10/B11a precedent). _run_llm_task and _listen_llm_input_queue did NOT
move — the task wrapper stays for B11c/d dispatch. Accommodations: seven mangled
spellings (store/do/execute_function_call/is_s2s/is_graph/is_knowledgebase/
process_stop_words), F541 ×3, noqa F841 ×2 / S110 ×2 / E501 ×9 on verbatim lines,
annotations + docstrings, otobaai loggers, and two lint-caught MISSING imports
added behavior-identically (`import aiohttp` was B11a's; here VoiceAIComponentError
+ LLMError via the bridge). Preserved quirks (R8): the stale end_of_llm_stream
clear, the hangup/completion gating, the eager-stub upsert contract, the
cancelled_at_ms stamp, the empty-final-buffer forward. generation.py lands at 983
lines (> 800 target, < 1,500 cap): the turn core is one auditing unit — flagged
residual for the B14 line-count audit (the B5 s2s_runner / B9a switcher / B10
history_sync precedent). task_manager.py: 5,267 → 4,556 lines; run() untouched,
goodbye-drain pin + A0 meta-test green.)

B11c: check=green (repo ruff + strict arch lint green; mypy/bandit/cov carry the
B10-noted environment gaps, unchanged); test-all=8/2629/2637 (net-new: +10 arch
tests, 2637 collected vs B11b's 2627; reconciliation: no legacy-tree test added,
removed, rewritten or repointed — none owed. The B1 characterization suite
(test_characterization_output_loop_invariants.py: the REAL-rebound nets for the
b"\\x00" passthrough, retired-chunk reset, replace-to-flush, stuck-gate release
and revalidate-in-kickoff) passes UNTOUCHED through the delegators (the B9b
precedent — it binds __process_output_loop via __get__ and mocks
_commit/_drop_staged as instance attrs, all of which keep resolving);
test_synthesize_silent_drops binds TaskManager._synthesize.__get__ and likewise
re-lands untouched; no suite patches an output-loop global at the task_manager
path while driving a moved body, so no R3 repoint was owed. Additive: +10 arch
tests in tests/arch/modules/voice/session/turn/test_output_loop.py (delegator +
session-injection pins per moved name, lookup-site pins, BLOCK-drop with null-byte
passthrough, SEND-commit, staged trio by sequence, silent-drop pipeline clear,
final-chunk ACK). The moves, proven AST-verbatim against HEAD modulo the declared
accommodations: final_chunk_played_observer, agent_hangup_observer,
__enqueue_chunk, __send_preprocessed_audio, _synthesize, __process_output_loop and
_inject_and_run_llm → voiceai/modules/voice/session/turn/output_loop.py (618
lines, under the 650 estimate) behind the OutputSession facade Protocol, via the
new adapters/output_runtime.py bridge (convert_to_request_log,
create_ws_data_packet, calculate_audio_duration, get_md5_hash,
get_raw_audio_bytes, mp3_bytes_to_pcm, resample, static_node_audio_key,
wav_bytes_to_pcm, yield_chunks_from_memory, STUCK_AUDIO_GATE_RELEASE_S;
SUPPORTED_SYNTHESIZER_MODELS rides voiceai.modules.voice.registry (B3) and
NON_NODE_RESPONSE_CATEGORIES rides voiceai.modules.voice.constants — moved there
from the tm module level with B11c per rule 1b, the B9a HANDOFF_CLIP_CACHE
precedent, with tm keeping the name by identity; LogComponent/LogDirection ride
voiceai.enums directly); tm keeps same-named thin delegators (mangled
_TaskManager__* spellings included) and injects itself (§3.1 bridge 3). In the
SAME step, _stage/_commit/_drop_staged_assistant_history complete history_sync.py
per the spec's "Region F + staged trio" map (new-home names strip the underscore;
HistorySession gains the staged ledgers). Left for B11d: _drop_all_staged (called
from _handle_transcriber_output), _retire_dropped_response (transcript path),
__send_first_message/__handle_accumulated_message (transcript-scheduled),
is_sequence_id_in_current_ids, _run_llm_task, _listen_llm_input_queue.
Accommodations: seven mangled spellings, F541 ×7, noqa F841 ×1 / S110 ×2 / E501
×14 / UP032 ×1 on verbatim lines, annotations + docstrings, otobaai loggers, and
three lint-caught MISSING imports added behavior-identically (traceback, uuid in
the output module; VoiceAIComponentError/LLMError were B11b's). Preserved quirks
(R8): the null-byte passthrough, retired-chunk reset, read-through-owner queue,
stuck-gate release, hangup-audio bypass, the commented-out legacy telemetry block.
task_manager.py: 4,556 → 4,106 lines; run() untouched, goodbye-drain pin + A0
meta-test green.)

B11d: check=green (repo ruff + strict arch lint green; mypy/bandit/cov carry the
B10-noted environment gaps, unchanged); test-all=8/2639/2647 (net-new: +10 arch
tests, 2647 collected vs B11c's 2637; reconciliation: 1 test rewritten in place,
1↔1, +0 removed. Rewrites: tests/test_end_call_bargein_guard.py
TestSourceGuards::test_listen_transcriber_uses_the_guard — the LAST
_listen_transcriber getsource pin re-lands under the SAME node id as a behavior
test proving the moved listener consults the ignore gate (seam-call recorded
while a barge-in arrives mid end_call actuation, cleanup assert-not-called; the
`import inspect` goes away with it). Same-commit patch repoints (R3):
test_browser_leg_transcripts.py's 6 convert_to_request_log patches and
test_substance_gate_foreign_max.py:166's monkeypatch → the transcript_listener
lookup site (both drive moved bodies: _handle_transcriber_output and the
_listen_transcriber eager branch); test_pre_call_webhook.py's remaining 9+9
patches stay at the task_manager path — they drive fire_pre_call_webhook, which
stays in tm. The end-call goodbye-audio, split-utterance, substance-gate,
same-turn-no-cancel and rule3a suites (all `__get__` rebinds / instance mocks)
pass UNTOUCHED through the delegators. The A0 meta-test needs NO update:
PINNED_METHOD_NAMES (`_listen_transcriber`, `_handle_transcriber_output`) keep
resolving through the delegators and the goodbye-drain substrings are untouched —
verified green. Additive: +10 arch tests in
tests/arch/modules/voice/session/turn/test_transcript_listener.py (delegator +
session-injection pins per moved name, lookup-site pins, ignore-gate truth
values, kickoff immediate-turn start, retire drop+sequence, task-type dispatch).
The moves, proven AST-verbatim against HEAD modulo the declared accommodations:
the task-type predicates + _extract_sequence_and_meta + _get_next_step +
_set_call_details, _process_followup_task, _should_ignore_transcriber_input,
_listen_llm_input_queue, _run_llm_task, process_transcriber_request,
_trigger_voicemail_check, _drop_all_staged_assistant_history,
_retire_dropped_response, kickoff_llm_generation, the regen-settle quartet,
_handle_transcriber_output, _end_call_on_component_error,
_log_transcriber_connection_error, _maybe_update_tts_language,
_listen_transcriber, __process_http_transcription, is_sequence_id_in_current_ids,
__send_first_message and __handle_accumulated_message (28 methods) →
voiceai/modules/voice/session/turn/transcript_listener.py (1,218 lines) behind
the ListenerSession facade Protocol, via the new adapters/listener_runtime.py
bridge (convert_to_request_log, create_ws_data_packet, safe_log_text,
LLM_REGEN_SETTLE_S, REGEN_SETTLE_EXCLUDED_TRANSCRIBERS, LLM_DEFAULT_CONFIGS,
clean_json_string, format_messages, format_error_message, VoiceAIComponentError,
LLMError, TranscriberError, TranscriberPool as a plain alias; asr_id_to_int rides
static_methods (B3)); tm keeps same-named thin delegators (mangled
_TaskManager__regen_after_settle / _TaskManager__process_http_transcription /
_TaskManager__send_first_message / _TaskManager__handle_accumulated_message
included) and injects itself (§3.1 bridge 3). New-home names strip the prefixes
(B7-B11c precedent). Accommodations: fourteen mangled spellings (regex-applied,
verified quad-free after the B11b/B11c loop bug recurred nowhere — grep-proof),
the should_ignore body delegating to the B7 hangup module by import (it was
already a delegator in tm), the floating welcome-accumulation string carried as
`#` comments (B018), five duplicate docstrings removed (verbatim original kept),
F541 ×19 (auto-fixed), noqa F841 ×2 / E501 ×~20 on verbatim lines, annotations +
docstrings, otobaai loggers, and four lint-caught MISSING imports added
behavior-identically (json, traceback, websockets in the module;
format_messages, clean_json_string, LLM_DEFAULT_CONFIGS, VoiceAIComponentError,
LLMError, TranscriberError, TranscriberPool, format_error_message via the
bridge). Preserved quirks (R8): the dtmf/single-consumer and browser-leg guards,
the eager overlap merge, the regen-settle absorb, the unconditional revalidate,
the welcome voicemail retire, the print_exc writes. transcript_listener.py lands
at 1,218 lines (> 800 target, < 1,500 cap) — the largest turn module, flagged
residual for the B14 line-count audit. task_manager.py: 4,106 → 3,242 lines;
run() untouched, goodbye-drain pin + A0 meta-test green. Turn core complete
(B11a-d): the engine's turn path now lives in session/turn/ (function_calls,
generation, output_loop, history_sync, transcript_listener).)

B12a: check=green (repo ruff + strict arch lint green; mypy/bandit/cov carry the
B10-noted environment gaps, unchanged); test-all=8/2643/2651 (net-new: +4 arch
tests, 2651 collected vs B11d's 2647; reconciliation: 3 test imports repointed
1↔1, +0 tests added/removed/rewritten. Repoints (R3): the
`import voiceai.output_handlers.telephony as telephony_module` in
test_telephony_output_send_timeout.py, test_cleanup_downstream_...socket.py and
test_characterization_default_io.py → the new io.output.telephony lookup site
(the OUTPUT_SEND_TIMEOUT_S reader moved); the known-failing pair ports as
known-failing (byte-identical `is_closed() is False` signature, verified against
baseline). Additive: +4 arch tests in tests/arch/modules/voice/io/
test_relocation.py (input/output shim-identity incl. talko and the sip_trunk
private parser, timeout lookup-site pin, package surfaces). The move, line-counted
per file against HEAD during the step: voiceai/input_handlers/** (10 files) +
voiceai/output_handlers/** (10 files, INCLUDING talko both legs) →
voiceai/modules/voice/io/input/** + io/output/** via the new
adapters/io_runtime.py bridge (create_ws_data_packet, calculate_audio_duration,
wav_bytes_to_pcm, IS_USER_ONLINE_MESSAGE, AUDIO_STREAM_END_SENTINELS,
UNCOMPRESSED_AUDIO_FORMATS, WEBCALL_TTS_SAMPLE_RATE); every old path is a
`# legacy-shim(spec-0004)` explicit-name identity re-export. Mechanical
accommodations beyond the bridge: configure_logger(__name__) → otobaai
get_logger(MODULE_NAME) (the B5 precedent; no suite pins handler logger names),
absolute intra-package imports rewritten (relative imports untouched),
`from voiceai.modules.voice.io...` in adapters/telephony.py (16 lines), and full
rule-6/7 normalization of all 20 files (293 annotations incl. `-> None` inference,
109 docstrings from per-name maps — no body touched; a script did the splicing,
ruffs F/B/UP autofixes plus hand noqas after). Deviations, made loud: (1) a REAL
regression caught mid-step — test_sip_trunk_hangup_drain's `fast_timings` fixture
patches constants on the MODULE object, and patching the shim is a silent no-op
(0.5s settle runs, 5 timing tests fail); fixed by repointing its
`sip_trunk_input` import to the new module, and the census was widened to
module-object patches (the only other hits were the 3 telephony_module files);
(2) B009 getattr-with-constant autofixed in sip_trunk input (provably guarded,
identical semantics); (3) 5 provably-unused stdlib imports dropped after
importer census; (4) moved io files keep legacy formatting (byte-identity for
review; s2s/session precedent — `make fmt` is not gate-enforced and the baseline
already carries 18 unformatted files). task_manager.py untouched (no imports of
handlers there); run() untouched.)

B12b: check=green; test-all=8/2647/2655 (net-new: +4 arch tests; reconciliation:
7 patch repoints 1↔1 (kalpa aiohttp.ClientSession ×4 → kalpa_http, kalpa
RESPONSE_IDLE_TIMEOUT + websockets.connect → kalpa_synthesizer, sarvam
aiohttp.ClientSession → sarvam), +0 added/removed. NOTE the spec's "4 kalpa"
undercounted: _patch_http + 3 more aiohttp sites all drive _generate_http, so 6
kalpa paths moved — honest count recorded here. The 2 known-failing telephony
tests keep failing identically (verified). Additive: +4 arch tests in
tests/arch/modules/voice/tts/test_relocation.py (shim identity incl. kalpa
_VOICE_IDS, kalpa split + lookup-site pins, pool identity). The moves:
voiceai/synthesizer/** (15 files) → voiceai/modules/voice/tts/{base,pool,stream}.py
+ tts/providers/*.py via the new adapters/tts_runtime.py bridge (transcoders,
packet builder, ssl context, scalar cache, SARVAM + MAYA maps, synth-format
probe); the kalpa split — _generate_http/synthesize/synthesize_telephony_clip/
_process_http_audio/_get_http_audio_format → tts/providers/kalpa_http.py (190
lines) with KalpaSynthesizer keeping thin same-named methods (MAX_TEXT_CHARS
stays in kalpa.py with the streaming sender; kalpa_http reads it through a
function-local import, the B4 precedent — a top-level import would cycle);
every old path is a `# legacy-shim(spec-0004)` identity re-export.
adapters/synthesis.py provider imports → new paths; language_runtime's
SynthesizerPool alias → tts.pool (identity unchanged; TranscriberPool alias waits
for B12c); task_manager.py:78-79 pool imports deliberately LEFT riding the shims
(B13a rewires setup). Full rule-6/7 normalization of all 16 files (548
annotations, 126 docstrings; B009/S110/S112/E501/F841 verbatim noqas).
Deviations, made loud: (1) test_maya_synthesizer.py:468 reads
`mod.websockets.connect` off the MODULE object — repointed to the new module
(the B12a sip_trunk lesson applied proactively); (2) three bridge additions found
by import failure, not census — get_synth_audio_format, SARVAM_* (caught), then
MAYA_* (caught by the layer-contract test, which is exactly what it is for);
(3) kalpa_synthesizer.py lands at 876 lines (> 800 target, < 1,500 cap) —
flagged residual; (4) the layer-contract test is now an explicit B12 gate
(it caught the maya miss). kalpa suite 87/87 green incl. the 4 repointed.

B12c: check=green; test-all=8/2650/2658 (net-new: +3 arch tests; reconciliation:
no legacy-tree patch/rewrite owed — zero string patches and zero module-object
patches target transcriber paths (census-verified), and every class-level importer
rides the shims. Additive: +3 arch tests in
tests/arch/modules/voice/asr/test_relocation.py (shim identity, deepgram split
seams, pool identity). The moves: voiceai/transcriber/** (15 files) →
voiceai/modules/voice/asr/{base,pool}.py + asr/providers/*.py via the new
adapters/asr_runtime.py bridge (packet/timing/audio helpers, ssl context, LID
classes, DEEPGRAM/REGEN/ELEVENLABS/OPENAI/SONIOX tunables); the deepgram 4-way
split — connection.py (315) + nova_session.py (514) + flux_session.py (321) as
module-level functions taking the transcriber as `self`, with transcriber.py
(405) keeping the class name/path/__init__/run/transcribe/turn-helpers plus thin
same-named delegators (async-generator senders/receivers re-yield, the B11b
precedent). New-home function names strip the underscore (B7-B12b precedent);
internal dispatch rides the facade delegators. ONE mangling accommodation:
self.__set_transcription_cursor → self._DeepgramTranscriber__set_transcription_cursor
(the facade keeps the method). self-annotation is `self: DeepgramTranscriber` via
TYPE_CHECKING (no runtime cycle; the B11d listener-Protocol alternative was
rejected as heavier); facade __init__ None-attrs gained precise annotations
(asyncio.Task | None etc.) for the mypy profile (unverifiable locally — env gap —
correct by construction). Full rule-6/7 normalization (451 annotations + 97
docstrings on providers/base/pool; deepgram modules annotated at build).
adapters/transcription.py provider imports → new paths (deepgram → the split
transcriber module); language/function/listener_runtime TranscriberPool aliases →
asr.pool. Gate suites green unchanged: B1 golden fixtures + flux +
turn-finalization + stuck-turn + pool trio (54 nodes). No fallback needed — the
R11 whole-move option stays documented but unused.

B12d (shim-inventory checkpoint): 58 true `# legacy-shim(spec-0004)` files (63
grep hits minus 5 prose mentions in new-module docstrings — io/asr/tts
__init__s, voice models, session/interruption); the layer-contract purity test
covers all of them (green). No new-arch file exceeds 1,500 lines. > 800-line
residuals, all flagged for the B14 audit: task_manager.py (3,242 — the in-progress
facade, ends ~900 at B13b), transcript_listener.py (1,218), switcher.py (1,139),
generation.py (983), history_sync.py (967), s2s_runner.py (891),
kalpa_synthesizer.py (876). io/mark_ledger.py + io/observables.py (304 + 48
lines, 16 importers) DEFERRED to the endgame helpers relocation — moving
MarkEventMetaData/ObservableVariable now would churn platform/tests for no
contract gain (the ports already type the seam; helpers DSP move is an explicit
non-goal). B12 leaves the tree with every leaf contract a proven port and every
legacy consumer riding shims.

B13a: check=green (repo ruff + strict arch lint green; mypy/bandit carry the B10-noted
environment gaps, unchanged); test-all=8/2658/2667 (net-new: +8 arch tests — 5
composition + 3 service prompt-seam; reconciliation: 1 patch repointed 1↔1, the B1
InterruptionManager double-construction spy → the composition lookup site (R3: the
constructor moved modules); test_llm_verbosity_passthrough passes UNMODIFIED).
Composition: Region D (tm 245-779, 535 lines) → voiceai/modules/voice/session/
composition.py (694 lines) as six source-order phases over one CallArgs bundle
(seed/adopt/wire-tasks/wire-state/primary/legs) via the new adapters/composition.py
bridge (VoicemailHandler, MarkEventMetaData, ObservableVariable,
ConversationHistory, LanguageDetector, LanguageSwitcher, WebhookAgent,
get_file_names_in_directory, ACCIDENTAL_INTERRUPTION_PHRASES; InterruptionManager
and ComponentLatencies ride their new-arch homes directly); all 172 statements
proven AST-verbatim modulo args-threading, fourteen mangled dispatches and the
adopt return. __init__ keeps its exact legacy dict signature and delegates;
from_components(CallArgs) is the alternate entry (parity-pinned). The tools dict
is still assigned (the turn seam reads through it) but built by composition.
register(): VoiceCallService gains optional session_store (AgentSessionStorePort,
already bound by the agents module; absent → None) and prefetches prompt payloads
through prompt_responses_from_store into the factory kwargs (forwarded only when
served, so B4-contract fakes keep working); the adapter forwards into
AssistantManager kwargs, which flow to load_prompt's EXISTING prompt_responses
kwarg — retiring the legacy get_prompt_responses branch in production (failure
falls back to it with a warning, never an exception). task_manager.py: 3,242 →
2,769 lines; run() untouched. cov gate REFINED (loudly, this step): arch-only
measurement capped at 46% after B12 moved provider code whose tests live in the
legacy tree and whose live-network branches can't run offline — the gate now runs
the full suite over new packages minus the leaf provider trees (io/asr/tts omit
in pyproject; providers stay behaviorally pinned by their green offline suites)
with exactly the 8 documented known failures deselected: 85.99% ≥ 85% green.
(.venv cannot run any gate here — it lacks the pinned deps; all B13a verification
ran on the system python, same tree.)

B13b: check=green (both lints; mypy/bandit carry the B10-noted gaps); test-all=
8/2658/2667 (net-new +0: 1 test rewritten in place 1↔1 — the goodbye-drain getsource
pin re-lands under the SAME node id driving the REAL drain at the coordinator seam
with wait-before-trim asserted on a concrete order list; the `import inspect` goes
away with it. The B6 run()-parity suite passes UNCHANGED over the swapped run(),
proving the delegation expression-for-expression). run() is edited for the first and
only time: (1) the drain block (gate + wait + terminal trim + heard reset) →
`await _voice_hangup.drain_hangup_goodbye(self)` (new lifecycle.hangup function +
CallLifecycle passthrough + tm delegator, position and order preserved);
(2) the Region V residue (latency wiring through master-strip, ~260 lines) →
`snapshot_teardown` + `build_conversation_report` / `build_followup_report` calls,
with the voicemail-cancel kept before the capture, the task-cancel appends and the
S3 recording block kept in place (I/O orchestration, not pure building), and the
llm-cancel-first finally head byte-untouched. The goodbye-drain pin retires from the
A0 meta-test with it (PINNED_METHOD_NAMES gains the drain_hangup_goodbye delegator
instead; module docstring updated). task_manager.py: 2,769 → 2,490 lines; measured
end-state 2,490 (spec estimate ~900 NOT met — justified residual, see B13c: the iron
rule keeps every same-named delegator until the endgame rename spec (~100 delegators
× ~4 lines), run() stays the coordinator by design (~250 lines incl. spawn/gather/
error arms), and the __setup_*/helper/observer methods stay until the composition
endgame. Deleting any of that here would break the harness census the tranche was
built around.)

B13c: check=green; test-all=8/2658/2667 (net-new +0: one arch-test assertion pair
retired with its dead write). Retirements, one concern per line-group: (i) the
`task_manager_instance` backref is BLOCKED, not dropped — grep-proof FAILED the way
the gate requires for deletion: tts/stream.py:365 still falls back to the backref
when no SequenceGatePort is injected, and provider/pool constructors plus a dozen
suites still pass it (dropping would change live synth-gate behavior; owned by the
endgame cutover). (ii) dead attrs, each grep-verified (production + harness +
getattr-string reads): DELETED should_respond, last_response_time,
allow_extra_sleep, consider_next_transcript_after (write-only composition seeds),
started_transmitting_audio (2 writes + HistorySession member; remaining harness
sets are harmless fixture state), llm_response_generated (3 writes + Generation
member; own arch test updated same-commit), first_message_passing_time (2 writes +
Listener member); KEPT synthesizer_queue (a B1 __new__ harness reads it — gate
fails, documented) and yield_chunks (production-read at the llm-queue spawn and
the output loop). No other B13c(ii) candidate exists: the nine named attrs are
exhaustively dispositioned above.

B14: check=green (repo ruff + strict arch lint green; mypy/bandit carry the B10-noted
environment gaps, unchanged); test-all=8 failed / 2,666 passed / 1 skipped /
2,675 collected (net-new: +8 arch tests — 5 controller + 1 patch-target + 2
line-count; reconciliation: 1 legacy pin rewritten 1<->1 — the B0 empty-router pin
flips to the flagged-route pin now that B14 lands the route it awaited. The
controller is otherwise purely additive). Closeout: (1) voice/controller.py lands the WS /chat/v1/{agent_id}
route on the app factory behind Environment.voice_ws_enabled (default dark, closes
4403; unknown agent closes 4404; served definitions run through VoiceCallService;
`.env.sample` documents VOICE_WS_ENABLED=0); the voice MODULE router is now the
controller's (agents-controller precedent). (2) New arch backstops, both green on
landing: tests/arch/test_patch_targets.py (every string patch/monkeypatch target in
the suite must resolve — the R3 B14 grep, mechanized) and
tests/arch/test_line_counts.py (hard cap 1,500 on new-arch files; the >800 set must
equal the flagged residuals — a new over-target file fails the build). The hard-cap
scope honestly excludes the legacy facade (capping it mid-strangler would freeze the
migration; its count is recorded per step instead). (3) Burn-down audited below:
58 true shims, zero deleted (the engine still runs on them; cutover is endgame) —
purity test green. (4) Line audit: nothing new-arch over 1,500; over-800 residuals
exactly the seven flagged (task_manager 2,490 counted separately). Final gates:
make check green (modulo the pinned env gaps), test-all stable at 8 failed (7 known
+ 1 env) / 2,666 passed, make sec clean (system python; bandit absent from .venv),
make cov 85.98% green under the B13a-refined gate. Tranche B closed: the realtime
runtime now lives in voiceai/modules/voice (common/ports/adapters, session/
composition+turn/language/lifecycle, io/asr/tts leaves, service+controller),
TaskManager is a 2,490-line facade (legacy signature __init__ + from_components,
run() coordinator, same-named delegators), and every legacy consumer rides shims.

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
- `voiceai/agent_manager/interruption_manager.py` (B10) — identity re-export of
  `session.interruption.InterruptionManager`. Remaining importers: task_manager.py:61
  (constructor), the interruption/lifecycle suites.
- `voiceai/{input,output}_handlers/**` (B12a, 20 files) — explicit-name identity
  re-exports of `voiceai.modules.voice.io`. Remaining importers: ~20 legacy-tree
  handler suites (class imports ride the shims), `tests/test_sip_trunk_hangup_drain.py`
  (module import), quickstart/platform construction paths, and the B12a relocation
  pins (deliberate). Three suites address the NEW timeout-constants lookup site.
- `voiceai/synthesizer/**` (B12b, 15 files) — explicit-name identity re-exports of
  `voiceai.modules.voice.tts` (kalpa split: the shim re-exports the facade, whose
  HTTP methods delegate to `kalpa_http`). Remaining importers: ~20 provider suites,
  task_manager.py:78-79 (pool construction, B13a-deferred), handoff/prewarm tests,
  and the B12b relocation pins (deliberate).
- `voiceai/transcriber/**` (B12c, 15 files) — explicit-name identity re-exports of
  `voiceai.modules.voice.asr` (deepgram shim re-exports the split facade).
  Remaining importers: ~20 transcriber suites (class imports), task_manager.py:78
  (pool construction, B13a-deferred), and the B12c relocation pins (deliberate).
- B14 audit result: 58 true shims (63 grep hits minus 5 prose mentions in new-module
  docstrings), ZERO deleted — every remaining importer above still resolves through
  them, and the layer-contract purity test plus the new patch-target test both pass.
  Deletions happen at cutover (endgame spec), never accreted.
