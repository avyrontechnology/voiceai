"""Module-level constants for the llms submodule.

Nothing in module code is hard-coded ad hoc: the environment key names
and default values backing every ``get_str`` fallback in this package
live here and are imported where needed.
"""

from __future__ import annotations

# --- Azure OpenAI ---
#: Environment variable carrying the Azure OpenAI endpoint URL.
AZURE_OPENAI_ENDPOINT_ENV = "AZURE_OPENAI_ENDPOINT"
#: Environment variable carrying the Azure OpenAI API key.
AZURE_OPENAI_API_KEY_ENV = "AZURE_OPENAI_API_KEY"
#: Environment variable carrying the Azure OpenAI API version.
AZURE_OPENAI_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
#: API version used when ``AZURE_OPENAI_API_VERSION`` is unset.
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-12-01-preview"

# --- LiteLLM ---
#: Environment variable carrying the LiteLLM model API key.
LITELLM_MODEL_API_KEY_ENV = "LITELLM_MODEL_API_KEY"
#: Environment variable carrying the LiteLLM model API base URL.
LITELLM_MODEL_API_BASE_ENV = "LITELLM_MODEL_API_BASE"
#: Environment variable carrying the LiteLLM model API version.
LITELLM_MODEL_API_VERSION_ENV = "LITELLM_MODEL_API_VERSION"

# --- Gemini ---
#: Preferred environment variable carrying the Gemini API key.
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
#: Fallback environment variable carrying the Gemini API key.
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"

# --- OpenAI ---
#: Environment variable carrying the OpenAI API key.
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
