"""Module-level constants for the transcriber submodule.

Nothing in module code is hard-coded ad hoc: environment key names and their
legacy default values live here and are imported where needed. Call sites read
values through ``voiceai.core.environment`` — never read directly.
"""

from __future__ import annotations

# ── TranscriberPool: legacy-flow LID mode ─────────────────────────────
LID_MODE_ENV_KEY = "LID_MODE"
DEFAULT_LID_MODE = "shadow"

# ── AssemblyAI ─────────────────────────────────────────────────────────
ASSEMBLY_API_KEY_ENV_KEY = "ASSEMBLY_API_KEY"

# ── Azure Speech ───────────────────────────────────────────────────────
AZURE_SPEECH_KEY_ENV_KEY = "AZURE_SPEECH_KEY"
AZURE_SPEECH_REGION_ENV_KEY = "AZURE_SPEECH_REGION"

# ── Deepgram ───────────────────────────────────────────────────────────
DEEPGRAM_AUTH_TOKEN_ENV_KEY = "DEEPGRAM_AUTH_TOKEN"
DEEPGRAM_HOST_ENV_KEY = "DEEPGRAM_HOST"
DEFAULT_DEEPGRAM_HOST = "api.deepgram.com"
DEEPGRAM_FLUX_HOST_ENV_KEY = "DEEPGRAM_FLUX_HOST"
DEFAULT_DEEPGRAM_FLUX_HOST = "api.deepgram.com"
DEEPGRAM_HOST_PROTOCOL_ENV_KEY = "DEEPGRAM_HOST_PROTOCOL"
DEFAULT_DEEPGRAM_HOST_PROTOCOL = "wss"

# ── ElevenLabs ─────────────────────────────────────────────────────────
ELEVENLABS_API_KEY_ENV_KEY = "ELEVENLABS_API_KEY"
ELEVENLABS_API_HOST_ENV_KEY = "ELEVENLABS_API_HOST"
DEFAULT_ELEVENLABS_API_HOST = "api.elevenlabs.io"

# ── Gemini (falls back to the shared Google key GeminiLLM reads) ───────
GEMINI_API_KEY_ENV_KEY = "GEMINI_API_KEY"
GOOGLE_API_KEY_ENV_KEY = "GOOGLE_API_KEY"

# ── Gladia ─────────────────────────────────────────────────────────────
GLADIA_API_KEY_ENV_KEY = "GLADIA_API_KEY"
GLADIA_HOST_ENV_KEY = "GLADIA_HOST"
DEFAULT_GLADIA_HOST = "api.gladia.io"

# ── OpenAI Realtime (EU host uses the EU-scoped key) ───────────────────
OPENAI_REALTIME_HOST_ENV_KEY = "OPENAI_REALTIME_HOST"
DEFAULT_OPENAI_REALTIME_HOST = "api.openai.com"
OPENAI_API_KEY_ENV_KEY = "OPENAI_API_KEY"
OPENAI_API_KEY_EU_ENV_KEY = "OPENAI_API_KEY_EU"

# ── Pixa ───────────────────────────────────────────────────────────────
PIXA_API_KEY_ENV_KEY = "PIXA_API_KEY"
PIXA_WS_HOST_ENV_KEY = "PIXA_WS_HOST"
DEFAULT_PIXA_WS_HOST = "transcript.heypixa.ai"

# ── Sarvam ─────────────────────────────────────────────────────────────
SARVAM_API_KEY_ENV_KEY = "SARVAM_API_KEY"
SARVAM_HOST_ENV_KEY = "SARVAM_HOST"
DEFAULT_SARVAM_HOST = "api.sarvam.ai"

# ── Smallest AI ────────────────────────────────────────────────────────
SMALLEST_API_KEY_ENV_KEY = "SMALLEST_API_KEY"
SMALLEST_HOST_ENV_KEY = "SMALLEST_HOST"
DEFAULT_SMALLEST_HOST = "api.smallest.ai"

# ── Soniox (default host lives in voiceai.constants; only the key is here)
SONIOX_API_KEY_ENV_KEY = "SONIOX_API_KEY"
SONIOX_HOST_ENV_KEY = "SONIOX_HOST"
