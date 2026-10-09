# Spec 0063: split transcript_listener.py (1271 lines) into focused files

## Goal

`voiceai/modules/voice/session/turn/transcript_listener.py` is 1271+ lines — the
largest file in the new architecture. It owns five distinct responsibilities:

1. **Protocol** — `ListenerSession` (the typed facade of the legacy session)
2. **Pure predicates** — `extract_sequence_and_meta`, `is_extraction_task`,
   `is_summarization_task`, `is_conversation_task`, `get_next_step`,
   `set_call_details`, `should_ignore_transcriber_input`
3. **Transcript routing** — `handle_transcriber_output` (the transcript router)
4. **Regen settle** — `arm_regen_settle`, `regen_after_settle`, `regen_settle_armed`,
   `regen_settle_can_fire` (the debounce quartet)
5. **Transcriber consumer** — `listen_transcriber` (the queue consumer loop)

Split into focused files, each with one responsibility. No logic change —
the split is purely mechanical.

## Non-goals

- No behavior change (existing tests pin behavior)
- No new features
- No interface changes (ListenerSession stays the same)
- Other large files (switcher.py, s2s_runner.py, etc.) split in later specs

## Interface contracts

All files live under `voiceai/modules/voice/session/turn/`. New module
`transcript_listener/__init__.py` re-exports the public surface (backward compat:
`from voiceai.modules.voice.session.turn.transcript_listener import listen_transcriber`
keeps working).

### File 1: `transcript_listener/protocols.py` (~120 lines)
- `ListenerSession` Protocol — moved VERBATIM (no change to members)

### File 2: `transcript_listener/predicates.py` (~80 lines)
- `extract_sequence_and_meta(self, message) -> tuple`
- `is_extraction_task(self) -> bool`
- `is_summarization_task(self) -> bool`
- `is_conversation_task(self) -> bool`
- `get_next_step(self, sequence, origin) -> str | None`
- `set_call_details(self, message) -> None`
- `should_ignore_transcriber_input(self) -> bool`

### File 3: `transcript_listener/regen.py` (~80 lines)
- `regen_settle_armed(self) -> bool`
- `regen_settle_can_fire(self) -> bool`
- `arm_regen_settle(self, transcriber_message, meta_info) -> None`
- `regen_after_settle(self) -> None`

### File 4: `transcript_listener/router.py` (~120 lines)
- `handle_transcriber_output(self, next_task, transcriber_message, meta_info) -> None`

### File 5: `transcript_listener/consumer.py` (~400 lines)
- `listen_transcriber(self) -> None` — the queue consumer loop (largest single function)

### File 6: `transcript_listener/__init__.py` (~40 lines)
- Re-exports: `ListenerSession`, all predicates, regen functions, `handle_transcriber_output`, `listen_transcriber`, `kickoff_llm_generation`, `run_llm_task`, `process_followup_task`, `process_transcriber_request`, `trigger_voicemail_check`, `drop_all_staged_assistant_history`, `retire_dropped_response`, `end_call_on_component_error`, `log_transcriber_connection_error`, `maybe_update_tts_language`, `listen_llm_input_queue`
- Module-level constants: `LLM_REGEN_SETTLE_S`, `REGEN_SETTLE_EXCLUDED_TRANSCRIBERS`

### Unchanged (stay in `transcript_listener.py` or move to existing files)
- `kickoff_llm_generation`, `run_llm_task`, `process_followup_task`, `process_transcriber_request` — these are LLM/task orchestration, re-exported from `__init__`
- `trigger_voicemail_check`, `drop_all_staged_assistant_history`, `retire_dropped_response` — re-exported
- `end_call_on_component_error`, `log_transcriber_connection_error`, `maybe_update_tts_language` — re-exported
- `listen_llm_input_queue` — re-exported

### Adapter-bound globals (R3)
`convert_to_request_log`, `create_ws_data_packet`, `safe_log_text`,
`LLM_REGEN_SETTLE_S`, `REGEN_SETTLE_EXCLUDED_TRANSCRIBERS` — bound into
`transcript_listener/__init__.py` via `adapters.listener_runtime` (same
mechanism as today, so monkeypatch string paths keep resolving).

## Data model

N/A — no persisted models touched.

## Security notes

N/A — no auth, secrets, or outbound calls touched.

## Test plan

- Existing `test_transcript_listener.py` + `test_turn.py` pass unchanged
- New: import surface test — `from voiceai.modules.voice.session.turn.transcript_listener import listen_transcriber` resolves
- New: file size gate — no file in `transcript_listener/` exceeds 500 lines

## Verification

```sh
make check
make sec
```

## Rollout

- `git mv transcript_listener.py transcript_listener/__init__.py` then split
- All imports updated via sed
- `test_module_shape.py` updated if it pins the old path
- Rollback = revert (mechanical split, tests pin behavior)

## Burn-down

- [x] Create `transcript_listener/` package with `__init__.py`
- [x] `protocols.py` — ListenerSession (129 lines)
- [x] `predicates.py` — 7 pure functions (81 lines)
- [x] `regen.py` — 4 regen functions (68 lines)
- [x] `router.py` — handle_transcriber_output + send_first_message (153 lines)
- [x] `consumer.py` — listen_transcriber + http_transcription + accumulated (476 lines)
- [x] `llm.py` — followup, llm_queue, run_llm, kickoff, errors (305 lines)
- [x] `__init__.py` re-exports + adapter-bound globals (105 lines)
- [x] Update all imports across voice module
- [x] `make check` green (2165 passed, bandit clean)
- [x] File size gate: all files ≤ 500 lines
