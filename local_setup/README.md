## Local docker setup

The single app (`uvicorn voiceai.app:app`, spec 0048) is the only server: agents,
prompts and every platform row live in MongoDB Atlas, Redis is an optional cache,
and there is no quickstart server any more. The Docker setup (`Dockerfile`,
`docker-compose.yml`, `start.sh`) lives at the repo root; one image serves every
service and `command` picks the process. Populate `.env` from `.env.sample` first
(every knob is commented there and declared in `voiceai/core/environment.py`).

### Services

| Service | Role | Reads from `.env` |
|---|---|---|
| `voiceai-app` | the API, port 5001, every route under `/api/v1` | `DB_BACKEND=mongo`, `MONGO_URL` (Atlas), `VOICE_WS_ENABLED=true`, `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`, `ALLOWED_ORIGINS`; optional `REDIS_CACHE_URL`, `TWILIO_AUTH_TOKEN`; provider keys |
| `twilio-app` | [Twilio](telephony_server/twilio_api_server.py) outbound trunk, host port 8011 | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` ([Twilio account](https://www.twilio.com/docs/usage/tutorials/how-to-use-your-free-trial-account)); `VOICEAI_API_KEY`, `VOICEAI_INTERNAL_URL` |
| `plivo-app` | [Plivo](telephony_server/plivo_api_server.py) outbound trunk, port 8002 | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER` ([Plivo account](https://www.plivo.com/)); `VOICEAI_API_KEY`, `VOICEAI_INTERNAL_URL` |
| `talko-app` | [Talko / Tata Tele](telephony_server/talko_api_server.py) trunk, port 8004 | `TALKO_API_BASE_URL`, `TALKO_API_KEY`, `TALKO_AI_DID`, `TALKO_PARTNER_ID` |
| `ngrok` | public tunnels for the Twilio/Plivo callbacks | `NGROK_AUTHTOKEN` (the agent reads it from the environment; [`ngrok-config.yml`](ngrok-config.yml) only defines the tunnels) |
| `redis` (`--profile cache`) | optional TTL cache: login throttle + JWT denylist | `REDIS_CACHE_URL=redis://redis:6379` |
| `mongo` (`--profile mongo`) | local MongoDB for development; production uses Atlas | `MONGO_URL=mongodb://mongo:27017` |

None of the telephony trunks uses Redis (the `redis.asyncio` import in the
Twilio/Plivo files is never called), so no service depends on the `redis`
container. Twilio and Plivo resolve their public URLs from `http://ngrok:4040`
by tunnel name — `ngrok-config.yml` ships `twilio-app` and `voiceai-app`
(`addr: voiceai-app:5001`); uncomment `plivo-app` for the Plivo trunk (free
ngrok plans cap concurrent tunnels). The file carries no authtoken: set
`NGROK_AUTHTOKEN` in `.env` and compose hands it to the agent. Talko needs
no tunnel: Tata streams to talko-service's static endpoint, which relays media
to `voiceai-app` over the compose network.

### Quick start

From the repo root:

```bash
cp .env.sample .env      # fill MONGO_URL, the JWT PEM pair, ALLOWED_ORIGINS, provider + telephony keys
chmod +x start.sh
./start.sh               # checks Docker, builds with BuildKit, `docker compose up -d`
```

`start.sh` starts every default service (`voiceai-app`, the three trunks, `ngrok`);
the `redis` and `mongo` profiles stay off unless asked for.

### Manual setup

1. Docker with Docker Compose V2.
2. BuildKit for faster builds:
   ```bash
   export DOCKER_BUILDKIT=1
   export COMPOSE_DOCKER_CLI_BUILD=1
   ```
3. Build: `docker compose build`
4. Run what you need:
   ```bash
   docker compose up -d voiceai-app                          # the API alone, against Atlas
   docker compose up -d voiceai-app twilio-app               # + Twilio trunk (starts ngrok)
   docker compose up -d voiceai-app plivo-app                # + Plivo trunk
   docker compose --profile cache up -d voiceai-app redis    # + optional Redis cache
   docker compose --profile mongo up -d voiceai-app mongo    # + local MongoDB (dev only)
   ```
5. Check: `curl http://localhost:5001/api/v1/health/live` (process) and
   `curl http://localhost:5001/api/v1/health/ready` (Atlas + Redis; the Redis probe
   follows `REDIS_CACHE_URL`, falls back to `REDIS_URL`, and reads `skipped` when
   both are empty). OpenAPI lives at
   `http://localhost:5001/docs`.

Once the containers are up, create agents through the `/api/v1` routes (see
`API.md` and `API_REFERENCE.md` at the repo root) and place calls through a
trunk, e.g. `POST http://localhost:8011/call` with
`{"agent_id": "...", "recipient_phone_number": "+1..."}`.

### Websocket contract for trunks

The app serves realtime media at `WS /api/v1/chat/v1/{agent_id}?ticket=<ticket>`
(dark unless `VOICE_WS_ENABLED=true`; the single-use ticket comes from
`POST /api/v1/auth/ws-ticket` and lives 60 seconds). Inbound Twilio calls are
answered by the app itself at `POST /api/v1/voice/inbound/twilio` (needs
`TWILIO_AUTH_TOKEN`). The Twilio/Plivo trunks mint the ticket in their answer
callback (`/twilio_connect`, `/plivo_connect`) — at answer time, not at dial
time, because ringing can outlast the ticket — with `VOICEAI_API_KEY`, an API
key carrying the `calls:write` scope (`POST /api/v1/api-keys` as the owner),
over `VOICEAI_INTERNAL_URL` (default `http://voiceai-app:5001`), and point the
carrier `<Stream>` at the ticketed URL on the `voiceai-app` tunnel. A failed
mint answers the carrier 500; no ticket-less URL leaves the trunk.

### Migrating from the retired quickstart/Redis deployment

`voiceai/platform/RUNBOOK.md`: census the legacy Redis, run the idempotent
backfill (`voiceai/tooling/backfill_upstash_to_atlas.py --census` / `--apply`),
switch traffic, re-run once.

## Example agents to create, use and start making calls
Go to the [Bolna examples](https://examples.bolna.dev/) to try out sample agents.
