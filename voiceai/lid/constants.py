"""Module-level constants for the lid submodule.

Nothing in module code is hard-coded ad hoc: the environment key names
and default values backing every ``get_str`` fallback in this package
live here and are imported where needed.
"""

from __future__ import annotations

# --- Sarvam ---
#: Environment variable carrying the Sarvam API key.
SARVAM_API_KEY_ENV = "SARVAM_API_KEY"
#: API key used when ``SARVAM_API_KEY`` is unset.
DEFAULT_SARVAM_API_KEY = ""
#: Environment variable carrying the Sarvam API host.
SARVAM_HOST_ENV = "SARVAM_HOST"
#: Host used when ``SARVAM_HOST`` is unset.
DEFAULT_SARVAM_HOST = "api.sarvam.ai"

# --- Soniox ---
#: Environment variable carrying the Soniox API key.
SONIOX_API_KEY_ENV = "SONIOX_API_KEY"
#: API key used when ``SONIOX_API_KEY`` is unset.
DEFAULT_SONIOX_API_KEY = ""
