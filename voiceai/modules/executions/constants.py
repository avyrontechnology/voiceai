"""Every literal the executions module uses: identity, routes, defaults, timings."""

from __future__ import annotations

from typing import Final

from voiceai.database.constants import Collections

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "executions"
EXECUTIONS_ROUTE_PREFIX: Final[str] = "/executions"
EXECUTIONS_TAG: Final[str] = "Executions"
CALLS_ROUTE_PREFIX: Final[str] = "/calls"
CALLS_TAG: Final[str] = "Calls"

# --- Persistence -----------------------------------------------------------------------
#: Call records. The member keeps its `PLATFORM_` prefix because the row name
#: predates this module; renaming it would orphan live call records, which are the
#: audit trail of who called whom and when (spec 0080).
EXECUTION_COLLECTION: Final[Collections] = Collections.PLATFORM_EXECUTIONS
EXECUTION_ID_PREFIX: Final[str] = "exec"

# --- Simulated-call timings ------------------------------------------------------------
#: Seconds of wall clock the simulated runner spends in each phase. Scaled by
#: `delay_scale`, so tests run the whole state machine instantly.
RING_DELAY_S: Final[float] = 0.3
TALK_DELAY_S: Final[float] = 1.0
#: Transcript turns exposed while a call is still in progress.
PARTIAL_TURNS: Final[int] = 3
#: The number a simulated call uses when the caller supplies none.
DEFAULT_TO_NUMBER: Final[str] = "+910000000000"

# --- Authorization scopes (unchanged from the legacy router, spec 0080) ----------------
READ_SCOPE: Final[str] = "platform:read"
WRITE_SCOPE: Final[str] = "platform:write"

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
#: `to_number` is PII and `variables` is customer content — ids only, never payloads.
LOG_SIMULATED: Final[str] = "Simulated call %s completed in %ss"
