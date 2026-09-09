# Agent Contract & Rules (Bolna Voice AI)

This file defines the contract, tech stack, active skills, and implementation loop that all AI agents must strictly follow when working on the `voiceai` (Bolna) project.

---

## 1. Project Context & Tech Stack

**Domain:** End-to-end production-ready framework for LLM-based voice-driven conversational applications.
**Core Architecture:** Asynchronous, streaming, modular pipeline (ASR -> LLM -> TTS -> Telephony over WebSockets).

**Primary Tech Stack:**
*   **Language:** Python >= 3.10
*   **Concurrency:** `asyncio` (heavy reliance on async generators and queues)
*   **Networking:** `websockets`, `aiohttp`, `fastapi`
*   **Testing:** `pytest`, `pytest-asyncio`
*   **Linting/Formatting:** `ruff` (configured in `pyproject.toml`)
*   **LLM Integration:** `litellm`, `openai`
*   **Media/Audio:** `pydub`, `audioop`

---

## 2. Active Agent Skills

The following skills from the Antigravity library are explicitly authorized and expected to be utilized during development:

### Core Engineering & Architecture
*   `python-pro`: Use for writing idiomatic, modern Python 3.10+ code (type hints, strict error handling).
*   `async-python-patterns`: CRITICAL. Master asyncio, concurrent programming, queues, and async/await patterns for high-performance, non-blocking I/O operations (streaming audio and LLM chunks).
*   `backend-architect`: Use for maintaining the modular provider architecture (extending new TTS/STT/Telephony providers).

### Testing & Quality
*   `python-testing-patterns`: Implement comprehensive tests with `pytest` and fixtures. Ensure async tests use `pytest-asyncio` correctly.
*   `tdd-workflows-tdd-cycle`: Follow strict Test-Driven Development (Red -> Green -> Refactor).

### Debugging & Reliability
*   `debugging-strategies`: Master systematic debugging for complex asynchronous race conditions, websocket drops, and memory leaks.
*   `error-handling-patterns`: Ensure graceful degradation across the streaming pipeline (e.g., if a synthesizer fails mid-stream).

---

## 3. Implementation Loop & Development Contract

Every time the agent implements a feature, fixes a bug, or refactors code, it MUST follow this strict execution loop:

### Phase 1: Context & Discovery
1.  **Understand the Request:** Clarify requirements.
2.  **Locate the Seam:** Identify the exact modular boundaries affected (e.g., is this a new `Synthesizer`, a modification to `TaskManager`, or an `InputHandler`?).
3.  **Check Existing Patterns:** Before writing new abstractions, review existing provider implementations (e.g., `bolna/synthesizer/elevenlabs_synthesizer.py`) and mimic their structure.

### Phase 2: Test-Driven Development (TDD)
1.  **Red (Write Test First):** Create or modify tests in the `tests/` directory to capture the new behavior or reproduce the bug.
2.  **Verify Failure:** Run the specific test using `pytest` to confirm it fails as expected.
3.  **Green (Implement):** Write the minimal, efficient, asynchronous Python code necessary to make the test pass.
4.  **Refactor:** Clean up the code. Ensure adherence to `ruff` linting rules (`line-length = 120`).

### Phase 3: Architectural Guardrails (Checklist)
Before declaring a task complete, verify:
*   [ ] **No Blocking Calls:** Are there any synchronous I/O or CPU-heavy tasks blocking the asyncio event loop? (e.g., `requests.get` instead of `aiohttp`, or un-yielded audio processing).
*   [ ] **Type Hints:** Are all new function signatures fully type-hinted?
*   [ ] **Error Bubbling:** Are exceptions caught and handled properly so they don't silently kill the streaming pipeline?
*   [ ] **Clean Imports:** Are imports organized and unused imports removed?

### Phase 4: Verification
1.  Run the full test suite: `pytest tests/`
2.  Run the linter: `ruff check .`
3.  Present a clean summary of the changes to the user.

---

## 4. Error Handling & Resilience Contract (mandatory)

Every failure that crosses a module boundary is a `voiceai.errors.VoiceAIError`; every transport renders that one object. Follow these rules in all new or touched code:

*   **Raise typed errors, never format them.** `ConfigurationError(message, path=...)` for bad agent/deployment config, `ProviderError` subclasses (`LLMError`, `SynthesizerError`, `TranscriberError`, `TelephonyError`, `S2SError`, `ToolCallError`) for component failures, `AgentNotFoundError` / `AuthenticationError` / `AuthorizationError` / `InvalidRequestError` / `DependencyUnavailableError` / `StorageError` for API paths. Wrap unknown exceptions with `classify_exception(exc, component=..., provider=..., model=...)`. `voiceai.exceptions` keeps the historical names for compatibility.
*   **One response shape.** HTTP error bodies are `voiceai.responses.ErrorEnvelope` (`ok`, `detail`, `error{code,message,error_id,retryable,component,details}`), installed once per app with `register_exception_handlers(app)`. Routes never build error JSON or pick status codes by hand; `HTTPException` still works but prefer the typed errors. Websocket failures use `ws_error_frame` / `close_with_error` (4xxx close codes mirror the HTTP status).
*   **Validate at the boundary.** `voiceai.agent_config.validate_agent_config(config)` runs before an agent is stored and before a call is built; it reports every issue with a path such as `tasks[0].tools_config.synthesizer.provider`. Provider lookups inside the engine raise `ConfigurationError` instead of calling `None`.
*   **Loops survive exceptions.** Long-lived coroutines wrap each iteration in `voiceai.helpers.resilience.iteration_guard` (or `supervise`) so one bad packet is logged with an `error_id` and skipped; only cancellation and an explicit `propagate` list escape. Background tasks go through a `TaskRegistry` (never a bare `asyncio.create_task` whose result is dropped) and are cancelled at teardown. `with_timeout` bounds every await on a provider; `call_soft` is for side paths (telemetry, cleanup) whose failure must never end a call.
*   **Never leak.** Secrets are not logged (redact `api_key`, `api_token`, `authorization`, headers); exception text is never spoken to a caller (use `constants.LLM_FAILURE_SPOKEN_MESSAGE`); unexpected exceptions reach clients only as `Internal error (ref <error_id>)`.
*   **Carrier trust.** Real phone legs authenticate the voice socket with a signed stream token (`voiceai.platform.stream_token`, `VOICE_STREAM_SECRET`); the dial endpoints require `TELEPHONY_API_KEY` once it is set (`voiceai.platform.carrier_auth`).

## 5. Specific Codebase Constraints

*   **Providers:** When adding a new provider (LLM, TTS, STT), it must be registered in `bolna/providers.py` and `bolna/enums.py`.
*   **Audio Handling:** Always be mindful of sample rates and encodings (e.g., linear16 vs. mulaw). Use the helper functions in `bolna/helpers/utils.py` for conversion.
*   **Logging:** Use the project's configured logger (`from bolna.helpers.logger_config import configure_logger`). Do not use `print` statements in production code.
