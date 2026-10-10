"""Every literal the webhooks module uses: identity, routes, defaults, log templates."""

from __future__ import annotations

from typing import Final

from voiceai.database.constants import Collections

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "webhooks"
WEBHOOKS_ROUTE_PREFIX: Final[str] = "/webhooks"
WEBHOOKS_TAG: Final[str] = "Webhooks"

# --- Persistence -----------------------------------------------------------------------
#: Collection holding the webhook registrations. The member keeps its `PLATFORM_`
#: prefix because the row name predates this module; renaming it would orphan
#: live rows (spec 0076).
WEBHOOK_COLLECTION: Final[Collections] = Collections.PLATFORM_WEBHOOKS

# --- Defaults -------------------------------------------------------------------------
DEFAULT_ENABLED: Final[bool] = True

# --- Authorization scopes (unchanged from the legacy router, spec 0076) ----------------
READ_SCOPE: Final[str] = "platform:read"
WRITE_SCOPE: Final[str] = "platform:write"

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
#: A webhook URL is operator infrastructure and can carry a token in its query
#: string, so only the id is ever logged.
LOG_CREATED: Final[str] = "Webhook created (id %s)"
LOG_DELETED: Final[str] = "Webhook %s deleted"
