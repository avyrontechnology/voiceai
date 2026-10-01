# Spec 0054: trunks on the single app

## Goal

The example carrier trunks under `local_setup/telephony_server/` were written
against the retired quickstart server (spec 0048). The Twilio and Plivo trunks
point the carrier media `<Stream>` at `{voiceai_host}/chat/v1/{agent_id}` — a
bare path the single app no longer serves — and send no credential, while the
single app's voice websocket (`WS /api/v1/chat/v1/{agent_id}`, spec 0021) closes
every socket that does not redeem a single-use `?ticket=` (close code 4401). A
call placed through these trunks therefore rings, is answered, and dies. After
this spec an answered Twilio or Plivo leg reaches the agent on the single app
with the tenant of the trunk's API key, the Talko trunk documents the contract
its relay must speak, the local microphone client mints its ticket, and the
ngrok config defines every tunnel a trunk looks up without carrying a token.

### What the investigation found (2026-10-01, read before changing the design)

1. **Only one credential opens the voice socket.** `voice_chat`
   (`voiceai/modules/voice/controller.py`) accepts, checks the dark flag (4403),
   reads `?ticket=` (`WS_TICKET_PARAM`), redeems it once through
   `AuthService.redeem_ticket`, requires `calls:write` on the redeemed principal
   (else 4401), binds that principal's tenant, and resolves the agent through the
   tenant-scoped port (unknown or foreign agent → 4404). No cookie, bearer header,
   path segment or first-frame credential is read.
2. **Tickets are minted only by `POST /api/v1/auth/ws-ticket`**
   (`AuthService.mint_ticket`: any authenticated principal with `calls:write` —
   session, JWT or API key; 60 s TTL; the answer is the standard envelope
   `{"ok": true, "data": {"ticket", "expires_in"}}`). `tests/arch/test_channel_gates.py`
   pins tickets to two homes (auth mints, the voice controller redeems): an
   app-side dialer that minted a ticket would be a third home.
3. **The app's place-call flow never calls the Twilio or Plivo trunk.**
   `POST /api/v1/calls/place` → `VoiceCallService.place_call` →
   `adapters/outbound.dial_trunk_call` → legacy `platform/talko_dialer.dial_via_talko`
   → `POST {TALKO_TRUNK_URL}/talko/call` with `{agent_id, recipient_phone_number,
   caller_did?, talko_api_key?}`. No stream URL and no ticket is handed over; the
   Talko trunk forwards `context_data: {voiceai_agent_id}` to talko-service, and
   talko-service (a separate repository) opens the media socket itself. The
   Twilio/Plivo trunks are operator-driven (`POST /call` on the trunk).
4. **The inbound webhook streams nothing.** `POST /api/v1/voice/inbound/twilio`
   validates `X-Twilio-Signature`, resolves the called number tenant-safely and
   answers TwiML `<Say>` or `<Reject/>` only; spec 0047's integration notes moved
   the `<Stream>` leg and "the ticket-mint design for carrier calls" out, and
   spec 0048 did not build them.
5. **Twilio drops query strings on `<Stream url>`.** Twilio's TwiML reference
   states that the `url` attribute "does not support query string parameters" and
   offers `<Parameter>` (delivered inside the `start` frame, after the socket is
   open). A `<Stream>` pointed at `…/chat/v1/{agent_id}?ticket=…` therefore
   arrives at the app without its ticket and is closed 4401. The round-1
   `deploy-config` note in spec 0048 (item 7: "points the carrier `<Stream>` at
   `wss://<tunnel>/api/v1/chat/v1/{agent_id}?ticket=…`") cannot work on Twilio and
   was never implemented in the trunk files; this spec supersedes it.
6. **talko-service still speaks the quickstart contract.** Its relay
   (`src/components/pstn/voiceai_relay.py`) mints with `resp.json()["ticket"]`
   (the single app nests it under `data`) and connects with `?token=` (the single
   app reads `?ticket=`). The UI playground (`obotaai-ui`) also sends `?token=`.
   Both live outside this repository.

## Decisions

1. **The trunk mints the ticket with an API key; the app's gate is consumed, not
   changed.** This is spec 0021 Decision 1 (the websocket credential is the
   single-use `?ticket=` minted by auth) plus its two-home tripwire, and it is the
   pattern talko-service already uses. The alternative — the app mints at
   place-call time and hands the trunk a complete stream URL — is rejected: the
   place-call flow does not reach these trunks (finding 3), a ticket minted at
   dial time expires while the phone rings (60 s TTL), and it would add a third
   ticket home. Tenant safety follows from the key: the ticket carries the tenant
   of the API key's owner, and the app resolves `agent_id` inside that tenant only.
2. **The trunk terminates the carrier media socket and relays it to the app over
   the private network.** The carrier `<Stream>` points at the trunk's own public
   tunnel, `wss://<trunk tunnel>/media/{call_ref}` (no query string — finding 5);
   when the carrier connects, the trunk mints the ticket and opens
   `ws://voiceai-app:5001/api/v1/chat/v1/{agent_id}?ticket=…` itself, then pumps
   frames verbatim both ways. Same mechanism for Twilio and Plivo (one code path,
   one test surface). Consequences: the ticket never appears in carrier-fetched
   XML or carrier logs, it is minted after answer (never stale), and no trunk
   needs the `voiceai-app` tunnel any more.
3. **The carrier leg authenticates to the trunk with a single-use call
   reference.** `POST /call` registers a pending dial (`agent_id`, the trunk's
   public base URL) under an unguessable `call_ref` (256 bit, 5 minute TTL,
   bounded registry); the answer callback only renders a `<Stream>` for a pending
   reference, and the media socket claims it exactly once. The callbacks no longer
   take `voiceai_host` or `agent_id` from the query string, so a caller of the
   public tunnel can neither choose the host a stream is sent to nor obtain a
   bridge to an agent without a dial this trunk placed.
4. **No alias for the retired contract.** The owner decision in spec 0048 stands
   (bare paths retire, clients are fixed): the app does not learn `?token=` and
   the trunks do not fall back to a ticket-less URL. A trunk without
   `VOICEAI_API_KEY` refuses `POST /call` before the carrier is dialed.

## Non-goals

- App-side changes. `voiceai/modules/voice/**` and `voiceai/modules/auth/**` are
  untouched (the voice module stays at its 49800-line budget; the route table and
  the ticket tripwire do not move).
- Inbound carrier media on the single app (the `<Stream>` leg of
  `POST /api/v1/voice/inbound/twilio`). It needs a ticket that binds the *agent's*
  tenant without a user principal — a new auth capability (burn-down).
- Carrier signature validation on the trunk callbacks (`X-Twilio-Signature`,
  `X-Plivo-Signature-V3`). The single-use `call_ref` is the callback credential;
  signatures are defence in depth for a production trunk, not for the example.
- Authenticating `POST /call` on the trunk (pre-existing: it is unauthenticated
  and reachable through the trunk's public tunnel — burn-down).
- The Talko trunk's request/response behaviour, and talko-service itself.
- Multi-worker trunks: the pending-dial registry is in-process; compose runs one
  uvicorn worker per trunk.

## Interface contracts

Files touched (this batch only): `local_setup/telephony_server/trunk_bridge.py`
(new, shared by the two trunks; uvicorn's `--app-dir` puts it on the path),
`local_setup/telephony_server/{twilio,plivo,talko}_api_server.py`,
`local_setup/quickstart_client.py`, `local_setup/ngrok-config.yml`,
`tests/test_telephony_trunk_tickets.py` (rewritten), `tests/test_trunk_stream_url.py`
(new), this spec.

### `trunk_bridge.py`

| Name | Contract |
| --- | --- |
| `BridgeSettings.from_env()` | reads `VOICEAI_API_KEY` and `VOICEAI_INTERNAL_URL` (default `http://voiceai-app:5001`); `require_configured()` raises `TrunkNotConfiguredError` (503) when the key is empty or the URL is not http(s) |
| `chat_url(internal_url, agent_id, ticket)` | `ws(s)://<app>/api/v1/chat/v1/<quoted agent_id>?ticket=<quoted ticket>` |
| `media_url(public_url, call_ref)` | `wss://<trunk tunnel>/media/<call_ref>` — never a query string |
| `answer_url(public_url, path, call_ref)` | `https://<trunk tunnel><path>?call_ref=<call_ref>` |
| `resolve_public_url(tunnel_name)` | ngrok agent API lookup by tunnel name, bounded by a timeout; `TunnelUnavailableError` (503) names the tunnel |
| `mint_ticket(settings)` | `POST {internal_url}/api/v1/auth/ws-ticket`, `Authorization: Bearer <key>`, bounded by a timeout; returns `data.ticket`; any failure raises `TicketMintError` |
| `PendingDials` | `register(agent_id, public_url) -> call_ref`, `peek(call_ref)`, `claim(call_ref)` (single use), TTL + capacity bound, thread-safe |
| `relay_media(carrier, call_ref, dials, settings)` | claim → accept → mint → connect → pump both ways → close both; an unknown reference is refused before accept and never mints |

### Trunk routes (Twilio port 8001, Plivo port 8002)

| Route | Before | After |
| --- | --- | --- |
| `POST /call` `{agent_id, recipient_phone_number}` | answer URL carried `voiceai_host` + `agent_id`; a carrier failure still answered `200 done` | 503 when the trunk has no API key or its tunnel is missing (carrier never dialed); answer URL carries only `call_ref`; a carrier refusal answers 502 with a fixed message |
| `POST /twilio_connect`, `POST /plivo_connect` | `?voiceai_host=&agent_id=` → `<Stream>` at the bare retired path | `?call_ref=` → `<Stream>` at `wss://<trunk tunnel>/media/{call_ref}`; unknown reference → 404, no XML |
| `WS /media/{call_ref}` | — | new: the carrier media socket, relayed to the app |
| `POST /plivo_hangup_callback` | unchanged | unchanged |

Environment (already present in `.env.sample`): `VOICEAI_API_KEY` — an API key
whose owner's role carries `calls:write` (the app re-checks the scope on the
redeemed *user*, so a `calls:write` key minted by a role without that scope is
denied at connect); `VOICEAI_INTERNAL_URL`.

### Talko trunk

No behaviour change. The module docstring states the single-app contract its
relay must speak (talko-service: `VOICEAI_API_BASE_URL` and `VOICEAI_WS_BASE_URL`
both ending in `/api/v1`, `data.ticket` from the mint envelope, `?ticket=` on the
socket).

### `quickstart_client.py`

Mints a ticket (`POST {VOICEAI_API_URL}/api/v1/auth/ws-ticket`, Bearer
`VOICEAI_API_KEY`) and connects to
`ws://…/api/v1/chat/v1/{ASSISTANT_ID}?ticket=…`; exits with a clear message when
the key or the assistant id is missing. Importing the module no longer opens
audio devices or the socket (`if __name__ == "__main__"`). The `extra_headers`
argument (removed in websockets 14+, pinned 15.0.1) is gone.

### `ngrok-config.yml`

Defines `twilio-app`, `plivo-app` (the names the trunks look up) and
`voiceai-app` (the app itself, for carrier webhooks such as
`POST /api/v1/voice/inbound/twilio`) — three tunnels, the free-plan cap. No
`authtoken` key: the agent reads `NGROK_AUTHTOKEN` from the environment.

## Data model

N/A — no collections, no persisted rows. Pending dials are process memory with a
TTL and a capacity bound.

## Security notes

- **Tenant binding:** the ticket is minted with the trunk's API key, so the call
  runs in the key owner's tenant; a foreign or unknown `agent_id` is closed 4404
  by the app's scoped lookup. One trunk process serves one tenant.
- **Secrets:** the API key is read from the environment only and sent only to
  `VOICEAI_INTERNAL_URL`. The ticket travels only on the private hop
  trunk → app; it is never rendered into carrier XML and never logged. `call_ref`
  is never logged. Logs carry the agent id and exception type names only — no
  phone numbers, no URLs with credentials.
- **Error opacity:** trunk responses carry fixed messages; no exception text or
  upstream body reaches the caller.
- **SSRF:** the hosts the trunk talks to come from the environment
  (`VOICEAI_INTERNAL_URL`) and the ngrok agent API, never from a request;
  `agent_id` and the ticket are percent-encoded into the URL.
- **Timeouts:** ngrok lookup, ticket mint, upstream websocket open and every
  relayed send are bounded.
- **Abuse bound:** the pending-dial registry is capped (503 when full) and
  expires entries, so unanswered dials cannot grow memory.
- **Committed ngrok token:** `local_setup/ngrok-config.yml` carried a real
  authtoken in git history. The file no longer has an `authtoken` key; **the
  owner must rotate that token in the ngrok dashboard** — removal from the
  working tree does not revoke it.
- **Known exposure, unchanged:** `POST /call` on a trunk is unauthenticated and
  published by the trunk's tunnel (burn-down).

## Test plan

Offline only (no carrier, no ngrok, no app; `httpx` and the websocket connector
are faked).

- `tests/test_trunk_stream_url.py`: URL builders (path, quoting, scheme mapping,
  no query string on the media URL); the helper's path and parameter equal the
  app's constants (`API_PREFIX`, `CHAT_WS_PATH`, `WS_TICKET_PARAM`, the auth
  router's ws-ticket route) so drift fails here; `mint_ticket` (bearer header,
  envelope, timeout, every failure shape); `PendingDials` (single use, TTL,
  capacity); `relay_media` (unknown reference refused without a mint; the minted
  ticket reaches the upstream URL; frames cross both ways; a failed mint or a
  failed connect closes the carrier and never opens the app socket; the ticket
  and the reference never reach the log); `resolve_public_url`; the ngrok config
  (no `authtoken`, every trunk's tunnel name defined); the microphone client's
  URL and mint.
- `tests/test_telephony_trunk_tickets.py`: both trunks through their real FastAPI
  apps — `/call` hands the carrier an answer URL with only `call_ref`; the answer
  callback renders the `/media/{call_ref}` stream and nothing from the retired
  contract; unknown reference → 404; no API key → 503 and no dial; carrier
  refusal → 502 without exception text; missing tunnel → 503.
- `tests/test_talko_api_server.py`: unchanged, still green.

## Verification

```sh
.venv/bin/python -m pytest -q tests/test_trunk_stream_url.py \
  tests/test_telephony_trunk_tickets.py tests/test_talko_api_server.py -p no:warnings
make lint
make lint-arch
make type
```

Live check (operator, needs carrier credentials): `docker compose up -d voiceai-app
twilio-app`, create an API key as a user whose role has `calls:write`, set
`VOICEAI_API_KEY`, `POST http://localhost:8011/call`, and expect `ws_connect` then
`call_recorded` rows in `GET /api/v1/auth/events`.

## Rollout

No flag, no migration. The trunks are example processes; restart them. Rollback
is revert. No shims registered.

## Burn-down

- [ ] Spec written before code (this file).
- [ ] `trunk_bridge.py`: URL builders, ticket mint, pending dials, relay.
- [ ] Twilio and Plivo trunks on the bridge; retired path and query-borne host gone.
- [ ] Talko trunk docstring states the single-app contract.
- [ ] `quickstart_client.py` mints its ticket; import has no side effects.
- [ ] `ngrok-config.yml`: all trunk tunnels defined, no token.
- [ ] Offline tests; gates.
