"""Module error-code namespace for the platform submodule.

Every domain failure the platform services can produce carries one stable
``platform.*`` code. The codes travel in log lines and structured
``details``; the wire ``code`` values from ``voiceai.errors`` are unchanged
(behavior frozen). Services reference these constants so the error surface
stays greppable from one file.
"""

from __future__ import annotations

BATCH_EXCEEDS_MAX_ENTRIES = "platform.batch_exceeds_max_entries"
BATCH_OUTSIDE_CALLING_HOURS = "platform.batch_outside_calling_hours"
BATCH_NO_FAILED_EXECUTIONS = "platform.batch_no_failed_executions"
BATCH_START_REPLAYED = "platform.batch_start_replayed"
BATCH_BACKGROUND_REFUSED = "platform.batch_background_refused"
BATCH_BACKGROUND_FAILED = "platform.batch_background_failed"
BATCH_RETRIED = "platform.batch_retried"
CAMPAIGN_EXCEEDS_MAX_ENTRIES = "platform.campaign_exceeds_max_entries"
CAMPAIGN_INVALID_STATE = "platform.campaign_invalid_state"
CAMPAIGN_OUTSIDE_CALLING_HOURS = "platform.campaign_outside_calling_hours"
UNKNOWN_SCOPES = "platform.unknown_scopes"
API_KEY_CREATED = "platform.api_key_created"
API_KEY_DELETED = "platform.api_key_deleted"
WORKSPACE_RESET = "platform.workspace_reset"
TEMPLATE_IMPORTED = "platform.template_imported"
GRAPH_DEPLOYED = "platform.graph_deployed"

ERROR_CODES = frozenset(
    {
        BATCH_EXCEEDS_MAX_ENTRIES,
        BATCH_OUTSIDE_CALLING_HOURS,
        BATCH_NO_FAILED_EXECUTIONS,
        BATCH_START_REPLAYED,
        BATCH_BACKGROUND_REFUSED,
        BATCH_BACKGROUND_FAILED,
        BATCH_RETRIED,
        CAMPAIGN_EXCEEDS_MAX_ENTRIES,
        CAMPAIGN_INVALID_STATE,
        CAMPAIGN_OUTSIDE_CALLING_HOURS,
        UNKNOWN_SCOPES,
        API_KEY_CREATED,
        API_KEY_DELETED,
        WORKSPACE_RESET,
        TEMPLATE_IMPORTED,
        GRAPH_DEPLOYED,
    }
)
