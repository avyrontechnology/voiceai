# Spec 0039: playground text chat on the HTTP chat endpoint (UI track)

## Goal

The playground chat tab (`src/components/playground/chat-talk.tsx`, today text
chat multiplexed over the voice websocket) migrates to the spec 0038 HTTP
endpoint: turns send as `POST /api/v1/chat/{agent_id}`, replies render as
they stream in over SSE, and the persisted per-agent history renders as the
conversation backlog. Chat-channel agents (which the voice socket answers
with 4404) become usable in the playground for the first time.

## Non-goals

- Voice-call UX changes (`live-talk.tsx` untouched; audio path multiplexing
  stays deferred per spec 0038).
- Chat billing/metering surfaces (backend has none yet — later spec).
- Optimistic multi-session management (one active session per agent view;
  session picker only if history proves it necessary).
- Inbound messaging integrations (WhatsApp/Telegram — later specs).

## Backend contract (frozen by spec 0038, slice 2)

- `POST /api/v1/chat/{agent_id}` with `{session_id?, message}` (strict
  pydantic) → `text/event-stream`, `data: <token>` frames plus terminal
  `data: [DONE]`.
- OPEN (backend, spec 0038 slice 2 follow-up): the SSE turn currently drops
  the minted `session_id` — no header, no frame — so an HTTP client cannot
  resume a conversation it just started. Until the backend surfaces it (an
  `x-session-id` response header or a leading JSON frame; header wins), the
  UI keeps the id only from the history list, and multi-turn continuation
  from a fresh POST is disabled, not guessed at.
- `GET /api/v1/chat/sessions?agent_id=` → `{ok, data: [...]}` history list,
  messages bounded at 100 per session (oldest drop, enforced on write).
- Unknown/foreign agent ids 404 without an oracle; non-chat agents 400;
  cross-agent session ids 404; anonymous callers 401 (session principal via
  middleware, no extra scope in v1).

## UI contracts

- New `src/services/platform/chat.ts`: `chatKeys` + two hooks following the
  `voices.ts` react-query pattern —
  `useChatHistory(agentId)` (`GET sessions`, `staleTime: 30s`, refetch on
  window focus off while a turn streams) and   `useSendChatTurn()` (mutation:
  `POST` turn through a fetch-reader that splits `data:` lines, appends token
  payloads to the in-flight turn, and resolves on `[DONE]`; abort signal
  cancels the reader on unmount or agent switch — no orphaned streams).
  Session continuation is wired only after the backend OPEN item above lands
  (the mutation then stores the surfaced `session_id` for the next turn;
  until then every fresh POST starts a session and history is read-only).
- New zod schemas in `src/lib/schemas/chat.ts` mirroring the backend shapes
  (turn request, history rows, SSE terminal sentinel; round-trip test against
  example payloads, talko parity style).
- Playground bindings (rework `chat-talk.tsx`, don't restyle):
  - Transport swap: replace the `{"type":"text"}` socket send + transcript
    frames with `useSendChatTurn`; the gating flips from "realtime agents
    only" to "agents whose `channels` include `chat`" (builder-owned field).
  - Streaming render: tokens append to the pending agent turn in place
    (single state update per frame batch, not per byte); `[DONE]` commits the
    turn and invalidates `chatKeys.history(agentId)`.
  - History view: on mount (and agent switch) the persisted history renders
    as the backlog above the live turns; the active `session_id` persists in
    component state (fresh mount per agent — parent `key` — is the reset,
    matching the existing pattern).
  - 400/404 handling: non-chat agent renders the "voice-only" notice the tab
    already shows for gated agents; 404 renders "agent unavailable" without
    echoing the id distinction (no oracle in the UI either).
- WS text-frame deprecation note: the `{"type":"text"}` multiplexing path
  stays live for voice calls (spec 0038 rollout: documented, not broken) but
  `chat-talk.tsx` no longer sends it; a code comment at the removed call
  site points at this spec, and any future text-in-call work gets its own
  protocol spec per the spec 0038 deferral.

## Test plan (UI track, jest)

Hook tests with mocked `apiClient`/fetch-reader (token frames accumulate;
`[DONE]` resolves with the session id; abort stops the reader; 404/400
propagate as typed errors); schema round-trips; playground interaction test
(send → pending turn streams → history invalidates; agent switch resets the
session; voice-only agent shows the notice, never the composer).

## Verification

```sh
cd ../voiceai-ui && npm test -- chat && npm run lint
```

## Rollout

UI-only, additive. The chat tab feature-detects the HTTP endpoint: when it
404s (old backend) the tab falls back to the current WS text-frame behavior
instead of version-gating, and is hidden for agents without a chat channel —
same grandfather posture as spec 0023's deprecated-provider UX.
