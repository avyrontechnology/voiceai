"""Module-level constants for the agent_types submodule.

Nothing in module code is hard-coded ad hoc: environment key names and
their default values live here and are imported where needed. The reads
themselves go through ``voiceai.core.environment`` (Constitution V), so
missing-vs-empty-vs-garbage semantics stay identical to the legacy
environment reads they replace.
"""

from __future__ import annotations

# --- LLM API keys ---
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
AZURE_OPENAI_API_KEY_ENV = "AZURE_OPENAI_API_KEY"
AZURE_OPENAI_ENDPOINT_ENV = "AZURE_OPENAI_ENDPOINT"
AZURE_OPENAI_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
GROQ_API_KEY_ENV = "GROQ_API_KEY"

#: Fallback Azure OpenAI API version when none is configured.
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-12-01-preview"

# --- Auxiliary LLM selection ---
CHECK_FOR_COMPLETION_LLM_ENV = "CHECK_FOR_COMPLETION_LLM"
VOICEMAIL_DETECTION_LLM_ENV = "VOICEMAIL_DETECTION_LLM"

#: Fallback model for voicemail detection aux LLMs.
DEFAULT_VOICEMAIL_DETECTION_LLM = "gpt-4.1-mini"

# --- RAG ---
RAG_SERVER_URL_ENV = "RAG_SERVER_URL"

#: Fallback RAG proxy server URL.
DEFAULT_RAG_SERVER_URL = "http://localhost:8000"

# --- Routing model selection (GraphAgent) ---
DEFAULT_ROUTING_MODEL_GROQ_ENV = "DEFAULT_ROUTING_MODEL_GROQ"
DEFAULT_ROUTING_MODEL_OPENAI_ENV = "DEFAULT_ROUTING_MODEL_OPENAI"
DEFAULT_ROUTING_MODEL_AZURE_ENV = "DEFAULT_ROUTING_MODEL_AZURE"

#: Fallback routing model per backend.
DEFAULT_ROUTING_MODEL_GROQ = "llama-3.3-70b-versatile"
DEFAULT_ROUTING_MODEL_OPENAI = "gpt-4.1-mini"
DEFAULT_ROUTING_MODEL_AZURE = "gpt-4.1-mini"

# --- Reasoning ---
GPT5_ROUTING_REASONING_EFFORT_ENV = "GPT5_ROUTING_REASONING_EFFORT"
