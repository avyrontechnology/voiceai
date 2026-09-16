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
