"""Module-level constants for the input_handlers submodule.

Nothing in module code is hard-coded ad hoc: shared literals live here and
are imported where needed.
"""

from __future__ import annotations

# --- SIP trunk teardown timing (input side) ---
#: Env key for post-HANGUP settle wait.
SIP_HANGUP_SETTLE_S_ENV: str = "SIP_HANGUP_SETTLE_S"
#: Default post-HANGUP settle wait (seconds, as env string).
DEFAULT_SIP_HANGUP_SETTLE_S: str = "0.5"

#: Env key for grace on top of the unplayed-audio estimate.
SIP_HANGUP_DRAIN_TIMEOUT_S_ENV: str = "SIP_HANGUP_DRAIN_TIMEOUT_S"
#: Default drain grace (seconds, as env string).
DEFAULT_SIP_HANGUP_DRAIN_TIMEOUT_S: str = "2.0"

#: Env key for hard ceiling on the whole teardown drain.
SIP_HANGUP_DRAIN_MAX_WAIT_S_ENV: str = "SIP_HANGUP_DRAIN_MAX_WAIT_S"
#: Default drain ceiling (seconds, as env string).
DEFAULT_SIP_HANGUP_DRAIN_MAX_WAIT_S: str = "30.0"

#: Env key for extra wait after QUEUE_DRAINED.
SIP_HANGUP_DRAIN_SETTLE_S_ENV: str = "SIP_HANGUP_DRAIN_SETTLE_S"
#: Default post-drain settle (seconds, as env string).
DEFAULT_SIP_HANGUP_DRAIN_SETTLE_S: str = "0.5"

# --- SIP trunk DTMF timing ---
#: Env key for DTMF inter-digit silence timeout.
SIP_DTMF_INTERDIGIT_TIMEOUT_S_ENV: str = "SIP_DTMF_INTERDIGIT_TIMEOUT_S"
#: Default DTMF inter-digit timeout (seconds, as env string).
DEFAULT_SIP_DTMF_INTERDIGIT_TIMEOUT_S: str = "3"

# --- Plivo auth (call-time fallback when auth_credentials omit them) ---
#: Env key for Plivo auth id fallback.
PLIVO_AUTH_ID_ENV: str = "PLIVO_AUTH_ID"

#: Env key for Plivo auth token fallback.
PLIVO_AUTH_TOKEN_ENV: str = "PLIVO_AUTH_TOKEN"

# --- Vobiz auth (call-time fallback when auth_credentials omit them) ---
#: Env key for Vobiz API key fallback.
VOBIZ_API_KEY_ENV: str = "VOBIZ_API_KEY"

#: Env key for Vobiz API secret fallback.
VOBIZ_API_SECRET_ENV: str = "VOBIZ_API_SECRET"
