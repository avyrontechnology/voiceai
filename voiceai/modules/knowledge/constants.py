"""Every literal the knowledge module uses: identity, routes, defaults, log templates."""

from __future__ import annotations

from typing import Final, Literal

from voiceai.database.constants import Collections

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "knowledge"
KNOWLEDGE_ROUTE_PREFIX: Final[str] = "/knowledgebases"
KNOWLEDGE_TAG: Final[str] = "Knowledge Bases"

# --- Persistence -----------------------------------------------------------------------
#: Collection holding the knowledge bases. The member keeps its `PLATFORM_` prefix
#: because the row name predates this module; renaming it would orphan live rows
#: (spec 0075).
KNOWLEDGE_COLLECTION: Final[Collections] = Collections.PLATFORM_KBS

# --- Defaults -------------------------------------------------------------------------
DEFAULT_STATUS: Final[str] = "ready"

#: The source kinds a knowledge base may reference.
KBSourceType = Literal["pdf", "url", "text"]

# --- Authorization scopes (unchanged from the legacy router, spec 0075) ----------------
READ_SCOPE: Final[str] = "platform:read"
WRITE_SCOPE: Final[str] = "platform:write"

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
#: KB names may be customer data, so only ids are logged.
LOG_CREATED: Final[str] = "Knowledge base created (id %s)"
LOG_ATTACHED: Final[str] = "Knowledge base %s attached to agent %s"
LOG_DETACHED: Final[str] = "Knowledge base %s detached from agent %s"
LOG_DELETED: Final[str] = "Knowledge base %s deleted"
