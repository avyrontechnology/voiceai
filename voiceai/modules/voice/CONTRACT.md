# voice CONTRACT

Owner: `squad-voice` (`#squad-voice`).

## Routes (mounted under `/api/v1`)

- `WS /chat/v1/{agent_id}` — realtime call loop (dark unless
  `Environment.voice_ws_enabled`, closes 4403/4404 otherwise).
- REST: `POST /calls/place` (202), Talko partner CRUD + preview/connect/refresh.
  Every route gates on a scope via `AuthService` (`calls:write`,
  `platform:read/write`, `agents:write`).

## Events

- in: telephony/carrier WS frames, `dtmf` + proactive `events` queues.
- out: execution record via `execution_recorder` (best-effort, warns and continues).

## Collections

- `executions` (`PlacedCall`, id = `execution_id`).
- `talko_partners` (`TalkoPartnerConfig`, id = `partner_id`; secrets never leave
  the model — views expose `key_configured` + last-4 hint only).

## Subpackages

- `ports/` — typed seams (transcription/synthesis/telephony/llm/s2s/outbound).
- `session/` (+ `turn/`, `language/`, `lifecycle/`) — per-turn pipeline + teardown.
- `asr/tts/io/s2s/` — verbatim strangler moves; legacy shims point here.
- `adapters/` — the ONLY files that may import legacy (`AgentManager`,
  providers, telephony handlers). Cross-module: agents surface only.
