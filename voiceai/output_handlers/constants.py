"""Module-level constants for the output_handlers submodule.

Nothing in module code is hard-coded ad hoc: shared literals live here and
are imported where needed.
"""

from __future__ import annotations

# --- Guarded socket sends ---
#: Env key bounding every media-socket send.
OUTPUT_SEND_TIMEOUT_S_ENV: str = "OUTPUT_SEND_TIMEOUT_S"
#: Default send bound (seconds, as env string).
DEFAULT_OUTPUT_SEND_TIMEOUT_S: str = "5"

# --- SIP trunk framing / playback (output side) ---
#: Env key for max Asterisk-bound WebSocket frame size.
SIP_MAX_WS_FRAME_BYTES_ENV: str = "SIP_MAX_WS_FRAME_BYTES"
#: Default max frame size (bytes, as env string).
DEFAULT_SIP_MAX_WS_FRAME_BYTES: str = "8000"

#: Env key for extra buffer after estimated playback end.
SIP_PLAYBACK_SETTLE_S_ENV: str = "SIP_PLAYBACK_SETTLE_S"
#: Default playback settle (seconds, as env string).
DEFAULT_SIP_PLAYBACK_SETTLE_S: str = "0.1"

#: Env key for max send rate as a multiple of real-time playback.
SIP_MAX_SEND_RATE_FACTOR_ENV: str = "SIP_MAX_SEND_RATE_FACTOR"
#: Default max send-rate factor (as env string).
DEFAULT_SIP_MAX_SEND_RATE_FACTOR: str = "1.5"

# --- Webcall (FreeSwitch) playback estimator ---
#: Env key for in-band mark settle delay.
WEBCALL_PLAYBACK_SETTLE_S_ENV: str = "WEBCALL_PLAYBACK_SETTLE_S"
#: Default playback settle (seconds, as env string).
DEFAULT_WEBCALL_PLAYBACK_SETTLE_S: str = "0.15"

#: Env key for estimator grace once mark echoes are confirmed.
WEBCALL_ESTIMATOR_GRACE_S_ENV: str = "WEBCALL_ESTIMATOR_GRACE_S"
#: Default estimator grace (seconds, as env string).
DEFAULT_WEBCALL_ESTIMATOR_GRACE_S: str = "1.5"
