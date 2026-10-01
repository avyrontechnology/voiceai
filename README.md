<h1 align="center">
</h1>
<p align="center">
  <p align="center"><b>End-to-end open-source voice agents platform</b>: Quickly build voice firsts conversational assistants through a json. </p>
</p>

<h4 align="center">
  <a href="https://discord.gg/59kQWGgnm8">Discord</a> |
  <a href="https://docs.bolna.ai">Hosted Docs</a> |
  <a href="https://bolna.ai">Website</a>
</h4>

<h4 align="center">
  <a href="https://discord.gg/59kQWGgnm8">
      <img src="https://img.shields.io/static/v1?label=Chat%20on&message=Discord&color=blue&logo=Discord&style=flat-square" alt="Discord">
  </a>
  <a href="https://github.com/bolna-ai/bolna/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="VoiceAI is released under the MIT license." />
  </a>
  <a href="https://github.com/bolna-ai/bolna/blob/main/CONTRIBUTING.md">
    <img src="https://img.shields.io/badge/PRs-Welcome-brightgreen" alt="PRs welcome!" />
  </a>
</h4>

> [!NOTE]
> We are actively looking for maintainers.

## Introduction

**[Bolna](https://bolna.ai)** is the end-to-end open source production ready framework for quickly building LLM based voice driven conversational applications.

## Demo
https://github.com/bolna-ai/bolna/assets/1313096/2237f64f-1c5b-4723-b7e7-d11466e9b226


## What is this repository?
This repository contains the entire orchestration platform to build voice AI applications. It technically orchestrates voice conversations using combination of different ASR+LLM+TTS providers and models over websockets.


## Components
VoiceAI helps you create AI Voice Agents which can be instructed to do tasks beginning with:

1. Orchestration platform (this open source repository)
2. Hosted APIs (https://docs.bolna.ai/api-reference/introduction) built on top of this orchestration platform [currently closed source]
3. No-code UI playground at https://platform.bolna.ai/ using the hosted APIs + tailwind CSS [currently closed source]


## Development philosophy
1. Any integration, enhancement or feature initially lands on this open source package since it forms the backbone of our Hosted APIs and dashboard
2. Post that we expose APIs or make changes to existing APIs as required for the same
3. Thirdly, we push it to the UI dashboard

```mermaid
graph LR;
    A[VoiceAI open source] -->B[Hosted APIs];
    B[Hosted APIs] --> C[Hosted Playground]
```

## Supported providers and models
1. Initiating a phone call using telephony providers like `Twilio`, `Plivo`, `Exotel` (coming soon), `Vonage` (coming soon) etc.
2. Transcribing the conversations using `Deepgram`, `Azure` etc.
3. Using LLMs like `OpenAI`, `DeepSeek`, `Llama`, `Cohere`, `Mistral`,  etc to handle conversations
4. Synthesizing LLM responses back to telephony using `AWS Polly`, `ElevenLabs`, `Deepgram`, `OpenAI`, `Azure`, `Cartesia`, `Smallest`, `Maya`, `Kalpa` etc.


Refer to the [docs](https://docs.bolna.ai/providers) for a deepdive into all supported providers.


## Running the server

`voiceai.app` is the only server (spec 0048): one process, MongoDB Atlas as the
system of record, Redis as an optional cache. There is no quickstart server any
more, and Redis no longer persists agents or prompts.

```bash
make setup                        # uv venv .venv --python 3.10 + dev deps
cp .env.sample .env               # fill MONGO_URL, the JWT PEM pair, ALLOWED_ORIGINS, provider keys
.venv/bin/uvicorn voiceai.app:app --host 0.0.0.0 --port 5001
```

- Every route is under `/api/v1` (`GET /api/v1/health/live`, `GET /api/v1/health/ready`;
  OpenAPI at `/docs`). Bare (un-prefixed) paths are gone.
- The realtime voice websocket is `WS /api/v1/chat/v1/{agent_id}?ticket=<ticket>`: dark
  unless `VOICE_WS_ENABLED=true`, and the single-use ticket comes from
  `POST /api/v1/auth/ws-ticket`. Inbound Twilio calls land on
  `POST /api/v1/voice/inbound/twilio` (needs `TWILIO_AUTH_TOKEN`).
- Required env: `DB_BACKEND=mongo`, `MONGO_URL` (Atlas; legacy alias `DB_URL`),
  `VOICE_WS_ENABLED=true`, `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` (RS256 PEM pair; both or
  neither), `ALLOWED_ORIGINS` (exact origins of the UI). Optional: `REDIS_CACHE_URL`
  (shared login throttle + JWT denylist; `REDIS_URL` is no longer required). Every knob
  is commented in `.env.sample` and declared in `voiceai/core/environment.py`.
- Gates: `make check` (lint, strict arch lint, mypy, tests, bandit) and `make sec`; CI
  runs the same after `make setup` (`.github/workflows/`).
- Migrating from the retired quickstart/Redis deployment: `voiceai/platform/RUNBOOK.md`
  (census, idempotent backfill via `voiceai/tooling/backfill_upstash_to_atlas.py`, switch).

## Local Docker setup (telephony examples)

`docker-compose.yml` runs the same image for every service; the full walkthrough is in
[`local_setup/README.md`](local_setup/README.md). Populate `.env` from `.env.sample` first.

| Service | What it is | Needs |
|---|---|---|
| `voiceai-app` | the single app above, port 5001 | `.env` (Atlas URL, JWT pair, origins, provider keys) |
| `twilio-app` / `plivo-app` | example outbound trunks ([Twilio](local_setup/telephony_server/twilio_api_server.py), [Plivo](local_setup/telephony_server/plivo_api_server.py)) | `TWILIO_*` / `PLIVO_*` keys, `VOICEAI_API_KEY` (an API key with the `calls:write` scope), the `ngrok` service |
| `talko-app` | [Talko / Tata Tele](local_setup/telephony_server/talko_api_server.py) trunk | `TALKO_API_BASE_URL`, `TALKO_API_KEY`, `TALKO_AI_DID`, `TALKO_PARTNER_ID` |
| `ngrok` | public tunnels for the Twilio/Plivo callbacks | `NGROK_AUTHTOKEN` in `.env` (tunnels in `local_setup/ngrok-config.yml`, no token there) |
| `redis` (`--profile cache`) | optional cache only, never a database | `REDIS_CACHE_URL=redis://redis:6379` |
| `mongo` (`--profile mongo`) | local MongoDB for development; production uses Atlas | `MONGO_URL=mongodb://mongo:27017` |

```bash
./start.sh                                    # or: docker compose build && docker compose up -d
docker compose up -d voiceai-app twilio-app   # the app + one trunk
docker compose --profile cache up -d          # add the Redis cache
```

None of the telephony trunks uses Redis. Twilio/Plivo resolve their public URLs from
`http://ngrok:4040/api/tunnels` by tunnel name (`twilio-app`, `plivo-app`, `voiceai-app`);
Talko needs no tunnel. When the carrier answers, the Twilio/Plivo trunks mint a single-use
ticket from `voiceai-app` (`POST /api/v1/auth/ws-ticket` with `VOICEAI_API_KEY`) and point
the carrier `<Stream>` at `wss://<voiceai-app tunnel>/api/v1/chat/v1/{agent_id}?ticket=…`.

## Example agents to create, use and start making calls
You may try out different agents from [example.bolna.dev](https://examples.bolna.dev).

## Programmatic usage (minimal example)

You can also build and run an agent directly in Python without the local telephony setup.

Example script: `examples/simple_assistant.py`

```python
import asyncio
from voiceai.assistant import Assistant
from voiceai.models import (
    Transcriber,
    Synthesizer,
    ElevenLabsConfig,
    LlmAgent,
    SimpleLlmAgent,
)


async def main():
    assistant = Assistant(name="demo_agent")

    # Configure audio input (ASR)
    transcriber = Transcriber(provider="deepgram", model="nova-2", stream=True, language="en")

    # Configure LLM
    llm_agent = LlmAgent(
        agent_type="simple_llm_agent",
        agent_flow_type="streaming",
        llm_config=SimpleLlmAgent(
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.3,
        ),
    )

    # Configure audio output (TTS)
    synthesizer = Synthesizer(
        provider="elevenlabs",
        provider_config=ElevenLabsConfig(voice="George", voice_id="JBFqnCBsd6RMkjVDRZzb", model="eleven_turbo_v2_5"),
        stream=True,
        audio_format="wav",
    )

    # Build a single coherent pipeline: transcriber -> llm -> synthesizer
    assistant.add_task(
        task_type="conversation",
        llm_agent=llm_agent,
        transcriber=transcriber,
        synthesizer=synthesizer,
        enable_textual_input=False,
    )

    # Stream results
    async for chunk in assistant.execute():
        print(chunk)


if __name__ == "__main__":
    asyncio.run(main())
```

How to run:

```bash
export OPENAI_API_KEY=...
export DEEPGRAM_AUTH_TOKEN=...
export ELEVENLABS_API_KEY=...
python examples/simple_assistant.py
```

This demonstrates orchestration and streaming output. For telephony, use the services in `local_setup/`.

Note: For REST-based usage (Agent CRUD over HTTP), see `API.md` in the repo root.

Expected output shape: `assistant.execute()` is an async generator yielding per-task result dicts (event-like chunks). The exact keys depend on configured tools/providers; treat it as a stream and process incrementally.

### Text-only pipeline example

If you want a text-only flow (no transcriber/synthesizer), you can enable a text-only pipeline:

Example script: `examples/text_only_assistant.py`

```python
import asyncio
from voiceai.assistant import Assistant
from voiceai.models import LlmAgent, SimpleLlmAgent


async def main():
    assistant = Assistant(name="text_only_agent")

    llm_agent = LlmAgent(
        agent_type="simple_llm_agent",
        agent_flow_type="streaming",
        llm_config=SimpleLlmAgent(
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.2,
        ),
    )

    # No transcriber/synthesizer; enable a text-only pipeline
    assistant.add_task(
        task_type="conversation",
        llm_agent=llm_agent,
        enable_textual_input=True,
    )

    async for chunk in assistant.execute():
        print(chunk)


if __name__ == "__main__":
    asyncio.run(main())
```

How to run (text-only):

```bash
export OPENAI_API_KEY=...
python examples/text_only_assistant.py
```

Expected output shape: `assistant.execute()` yields streaming dicts per task step; fields vary by configuration. Handle chunk-by-chunk.


## Using your own providers
You can populate the `.env` file to use your own keys for providers.

<details>

<summary>ASR Providers</summary><br>
These are the current supported ASRs Providers:

| Provider     | Environment variable to be added in `.env` file |
|--------------|-------------------------------------------------|
| Deepgram     | `DEEPGRAM_AUTH_TOKEN`                           |

</details>
&nbsp;<br>

<details>
<summary>LLM Providers</summary><br>
VoiceAI uses LiteLLM package to support multiple LLM integrations.

These are the current supported LLM Provider Family:
https://github.com/bolna-ai/bolna/blob/10fa26e5985d342eedb5a8985642f12f1cf92a4b/bolna/providers.py#L30-L47

For LiteLLM based LLMs, add either of the following to the `.env` file depending on your use-case:<br><br>
`LITELLM_MODEL_API_KEY`: API Key of the LLM<br>
`LITELLM_MODEL_API_BASE`: URL of the hosted LLM<br>
`LITELLM_MODEL_API_VERSION`: API VERSION for LLMs like Azure

For LLMs hosted via VLLM, add the following to the `.env` file:<br>
`VLLM_SERVER_BASE_URL`: URL of the hosted LLM using VLLM

</details>
&nbsp;<br>

<details>

<summary>TTS Providers</summary><br>
These are the current supported TTS Providers:
https://github.com/bolna-ai/bolna/blob/c8a0d1428793d4df29133119e354bc2f85a7ca76/bolna/providers.py#L7-L14

| Provider   | Environment variable to be added in `.env` file  |
|------------|--------------------------------------------------|
| AWS Polly  | Accessed from system wide credentials via ~/.aws |
| Elevenlabs | `ELEVENLABS_API_KEY`                             |
| OpenAI     | `OPENAI_API_KEY`                                 |
| Deepgram   | `DEEPGRAM_AUTH_TOKEN`                            |
| Cartesia   | `CARTESIA_API_KEY`                            |
| Smallest   | `SMALLEST_API_KEY`                            |
| Maya       | `MAYA_API_KEY`                            |
| Kalpa      | `KALPA_API_KEY`                            |

</details>
&nbsp;<br>

<details>

<summary>Telephony Providers</summary><br>
These are the current supported Telephony Providers:

| Provider | Environment variable to be added in `.env` file                                                                                                                    |
|----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Twilio   | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`|
| Plivo    | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER`|

</details>

## Open-source v/s Hosted APIs
**We have in the past tried to maintain both the open source and the hosted solution (via APIs and a UI dashboard)**.

We have fluctuated b/w maintaining this repository purely from a point of time crunch and not interest.

Currently, we are continuing to maintain it for the community and improving the adoption of Voice AI.

Though the repository is completely open source, you can connect with us if interested in managed hosted offerings or more customized solutions.
<a href="https://calendly.com/voiceai/30min"><img alt="Schedule a meeting" src="https://cdn.cookielaw.org/logos/122ecfc3-4694-42f1-863f-2db42d1b1e68/0bcbbcf4-9b83-4684-ba59-bc913c0d5905/c21bea90-f4f1-43d1-8118-8938bbb27a9d/logo.png" /></a>

## Extending with other Telephony Providers
In case you wish to extend and add some other Telephony like Vonage, Telnyx, etc. following the guidelines below:
1. Make sure bi-directional streaming is supported by the Telephony provider
2. Add the telephony-specific input handler file in [input_handlers/telephony_providers](https://github.com/bolna-ai/bolna/tree/master/bolna/input_handlers/telephony_providers) writing custom functions extending from the [telephony.py](https://github.com/bolna-ai/bolna/blob/master/bolna/input_handlers/telephony.py) class
   1. This file will mainly contain how different types of event packets are being ingested from the telephony provider
3. Add telephony-specific output handler file in [output_handlers/telephony_providers](https://github.com/bolna-ai/bolna/tree/master/bolna/output_handlers/telephony_providers) writing custom functions extending from the [telephony.py](https://github.com/bolna-ai/bolna/blob/master/bolna/output_handlers/telephony.py) class
   1. This mainly concerns converting audio from the synthesizer class to a supported audio format and streaming it over the websocket provided by the telephony provider
4. Lastly, you'll have to write a dedicated server like the example [twilio_api_server.py](https://github.com/bolna-ai/bolna/blob/master/local_setup/telephony_server/twilio_api_server.py) provided in [local_setup](https://github.com/bolna-ai/bolna/blob/master/local_setup/telephony_server) to initiate calls over websockets.

## Security Acknowledgments
We would like to thank the following individuals for responsibly disclosing security vulnerabilities and helping us keep this project safe:

- [@anaskhan54](https://github.com/anaskhan54) — June, 2026

## Contributing
We love all types of contributions: whether big or small helping in improving this community resource.

1. There are a number of [open issues present](https://github.com/bolna-ai/bolna/issues) which can be good ones to start with
2. If you have suggestions for enhancements, wish to contribute a simple fix such as correcting a typo, or want to address an apparent bug, please feel free to initiate a new issue or submit a pull request
2. If you're contemplating a larger change or addition to this repository, be it in terms of its structure or the features, kindly begin by creating a new issue [open a new issue :octocat:](https://github.com/bolna-ai/bolna/issues/new) and outline your proposed changes. This will allow us to engage in a discussion before you dedicate a significant amount of time or effort. Your cooperation and understanding are appreciated
