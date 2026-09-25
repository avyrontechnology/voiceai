# Spec 0038: Phase C — chat runtime (chat module + channel flip)

## Goal

Agents with `channels=["chat"]` serve text-in/text-out conversations over a
new HTTP SSE endpoint backed by a persisted per-agent history. Voice behavior
is untouched; WS text-frame multiplexing is explicitly deferred (rationale
below).

## Non-goals

- WS `{"type":"text"}` multiplexing into voice calls (deferred: turn
  interleaving + attribution need their own protocol spec; playground
  migrates to the HTTP endpoint — UI track item).
- Inbound messaging integrations (WhatsApp/Telegram — later specs).
- Voice/audio on the chat path; realtime hot-path changes.
- Chat billing/metering (later spec).

## Decisions (approved)

1. Transport: HTTP POST SSE primary (standard auth via middleware,
   tenant-bound by construction).
2. History persisted per-agent, tenant-scoped (`chat_sessions` collection).
3. Explicit `chat` pipeline value (not `asr` reuse): self-documenting,
   validated, engine-switchable.

## Interface contracts

**Schema (agents module, Slice 1 — owner: integrator).**
- `Pipeline = Literal["asr", "s2s", "chat"]` (+ `PIPELINE_CHAT` constant).
- `Task.pipeline="chat"` requires `task_type=="conversation"` (same rule).
- `resolve_pipeline_for_task`: explicit wins (any of the three), absent
  infers legacy (unchanged).
- Audit walk (`audit_provider_config`): `chat`-pipeline tasks MUST NOT carry
  `transcriber`/`synthesizer`/`s2s` blocks (reject if present — chat is
  LLM-only); `llm_agent` leaves validated as today; `llm_agent` REQUIRED
  (a chat task with no brain is incomplete → 400).
- Service channel allowlist `{voice}` → `{voice, chat}`.
- Voice WS gate unchanged (non-voice → 4404; chat-only agents stay 404
  there — no oracle, no new codes).

**Chat module (Slice 2 — owner: Agent A).** New `modules/chat/`:
- `models.py`: `ChatSession(BaseFields)` (`session_id` natural key,
  `agent_id`, `messages[]` of `{role, content, ts}`, bounded at 100 —
  oldest drop), `ChatMessage` submodel.
- `repository.py`: `ChatSessionsRepository` over scoped `BaseRepository`
  (natural-key pin, agent filter).
- `service.py`: `ChatService(repository, agent_store, llm)` —
  `post_message(session_id|None, agent_id, principal, content)`:
  resolve agent through scoped definitions (unknown/foreign → 404 no
  oracle; non-chat channel → 400 channel mismatch), load-or-create session
  (session must belong to agent + tenant or 404), append user msg, run LLM
  turn via the injected `llm` port (same `LlmPort` shape agents runtime
  uses — container wires the extraction LLM? NO: chat needs a conversational
  LLM runner — use `agents.adapters.llm.generate_extraction_text`? NO:
  single-turn completion port — define `ChatLlmPort` in chat `ports.py`
  `(messages) -> str`; container wires a thin adapter over the configured
  default LLM), append + persist assistant msg, return `{session_id,
  reply}`. History cap enforced on write.
- `controller.py`: `POST /chat/{agent_id}` (SSE `text/event-stream`,
  `data: <token>` frames + terminal `data: [DONE]`); `GET
  /chat/sessions?agent_id=` (history list, bounded); request body
  `{session_id?, message}` strict pydantic. Auth: session principal via
  middleware (no extra scope beyond authenticated in v1).
- `errors.py`: `ChatError`, `ChatNotFoundError` (404), `ChatChannelError`
  (400 non-chat agent).
- `container.py`: `_build_chat_service` (Factory, scoped views) +
  provider; `core/container.py` wire list + lifespan untouched.
- `__init__.py` registry: `chat.MODULE` (integrator edits
  `modules/__init__.py` + `test_registry.py` — shared files stay with
  the integrator).
- `database/constants.py`: `CHAT_SESSIONS = "chat_sessions"`
  (integrator edits — shared file).
- Backfill census: `chat_sessions` → new collection, no legacy rows;
  census test lists it under a `NEW_COLLECTIONS` exempt set? NO — census
  demands exactly one rule; new collections with no legacy rows go to
  DEFAULT_STAMPED (harmless, never matches). Integrator edits.

**Wiring + tests (Slice 3 — owner: Agent B).**
- `tests/arch/test_registry.py` + `modules/__init__.py` + backfill
  census + `database/constants.py`: integrator-owned shared files —
  REASSIGNED to integrator (me), NOT Agent B, to avoid collisions.
- Agent B owns: `modules/chat/tests/*` (service units with fakes:
  channel mismatch, foreign agent/session, history cap, SSE framing via
  factory + httpx), `tests/arch/test_channel_gates.py` extension?
  NO — that file is tripwire-stable; instead new
  `voiceai/modules/agents/tests/test_chat_channels.py`? Hmm — simpler:
  Agent B owns `modules/chat/tests/*` ONLY, plus `specs/0039-ui-chat.md`
  (UI sync: playground migration to HTTP endpoint, streaming render,
  history view, WS text-frame deprecation note).
- SSE framing test via httpx ASGI (collect `data:` lines; assert terminal
  `[DONE]`).

## Data model

`chat_sessions` collection. Tenant-stamped rows; `id` = `session_id`
(`ses_` prefix via `new_id`). Bounded messages (100, oldest drop).
No migration (new collection).

## Security notes

- Session ownership: `session.agent_id` + tenant must match the addressed
  agent on every post (confused-deputy guard — pinned by test).
- SSE: no secrets in frames; timeouts on LLM calls (existing port bounds).
- Prompt-injection surface unchanged (user content already flows in voice).
- `make sec` clean.

## Test plan

Slice 1: schema/inference/allowlist/walk unit tests. Slice 2 (Agent A):
service units (fakes) + repository units (memory). Slice 3 (Agent B):
factory endpoint tests (SSE framing, history, 404/400 contracts) +
UI spec. Integrator: registry/census wiring + full gate.

## Verification

```sh
make check
make sec
```

## Rollout

Additive module + endpoint; voice untouched. Rollback is revert. UI
playground migrates to HTTP in its own track (WS text frames keep legacy
behavior until then — documented, not broken).

## Burn-down

- [ ] Slice 1: schema + validation + allowlist (integrator).
- [ ] Slice 2: chat module (Agent A).
- [ ] Slice 3: wiring/tests owned splits + UI spec (integrator + Agent B).
