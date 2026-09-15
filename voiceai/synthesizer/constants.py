"""Module-level constants for the synthesizer submodule.

Nothing in module code is hard-coded ad hoc: environment key names and their
default values live here and are imported where needed. All reads go through
the ``voiceai.core.environment`` accessors, so the process environment is never
touched at the call sites.
"""

from __future__ import annotations

# --- ElevenLabs ---
ELEVENLABS_API_KEY_ENV = "ELEVENLABS_API_KEY"
ELEVENLABS_API_HOST_ENV = "ELEVENLABS_API_HOST"
DEFAULT_ELEVENLABS_API_HOST = "api.elevenlabs.io"

# --- OpenAI ---
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

# --- Azure Speech ---
AZURE_SPEECH_KEY_ENV = "AZURE_SPEECH_KEY"
AZURE_SPEECH_REGION_ENV = "AZURE_SPEECH_REGION"

# --- Cartesia ---
CARTESIA_API_KEY_ENV = "CARTESIA_API_KEY"
CARTESIA_API_HOST_ENV = "CARTESIA_API_HOST"
DEFAULT_CARTESIA_API_HOST = "api.cartesia.ai"

# --- Deepgram ---
DEEPGRAM_HOST_ENV = "DEEPGRAM_HOST"
DEFAULT_DEEPGRAM_HOST = "api.deepgram.com"
DEEPGRAM_AUTH_TOKEN_ENV = "DEEPGRAM_AUTH_TOKEN"

# --- Rime ---
RIME_API_KEY_ENV = "RIME_API_KEY"

# --- Smallest AI ---
SMALLEST_API_KEY_ENV = "SMALLEST_API_KEY"

# --- Sarvam ---
SARVAM_API_KEY_ENV = "SARVAM_API_KEY"

# --- Pixa ---
PIXA_API_KEY_ENV = "PIXA_API_KEY"
PIXA_TTS_HOST_ENV = "PIXA_TTS_HOST"
DEFAULT_PIXA_TTS_HOST = "hindi.heypixa.ai"

# --- Maya ---
MAYA_API_KEY_ENV = "MAYA_API_KEY"
MAYA_API_HOST_ENV = "MAYA_API_HOST"
DEFAULT_MAYA_API_HOST = "tts.mayaresearch.ai"

# --- Kalpa ---
KALPA_API_KEY_ENV = "KALPA_API_KEY"
KALPA_API_HOST_ENV = "KALPA_API_HOST"
DEFAULT_KALPA_API_HOST = "api.kalpalabs.ai"

# --- AWS Polly (standard AWS SDK variables; no defaults) ---
AWS_ACCESS_KEY_ID_ENV = "AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY_ENV = "AWS_SECRET_ACCESS_KEY"
AWS_REGION_ENV = "AWS_REGION"
