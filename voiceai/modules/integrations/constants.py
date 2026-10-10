"""Every literal the integrations module uses: identity, routes, vocabulary, defaults."""

from __future__ import annotations

from typing import Final, Literal

from voiceai.database.constants import Collections

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "integrations"
INTEGRATIONS_ROUTE_PREFIX: Final[str] = "/integrations"
INTEGRATIONS_TAG: Final[str] = "Integrations"

# --- Persistence -----------------------------------------------------------------------
#: Collection holding the integrations. The member keeps its `PLATFORM_` prefix
#: because the row name predates this module; renaming it would orphan live
#: credentials (spec 0077).
INTEGRATION_COLLECTION: Final[Collections] = Collections.PLATFORM_INTEGRATIONS

# --- Vocabulary ------------------------------------------------------------------------
#: The providers an integration can be. Closed, carried over verbatim from the
#: legacy model (spec 0077) — widening it is a separate, reviewed decision.
IntegrationKind = Literal[
    "twilio", "plivo", "exotel", "vobiz", "talko", "calcom", "n8n", "zapier", "sheets", "sip", "truecaller"
]

# --- Defaults -------------------------------------------------------------------------
DEFAULT_ENABLED: Final[bool] = True

# --- Credential masking ----------------------------------------------------------------
#: What a secret config value is replaced with on the way out.
MASKED_SECRET: Final[str] = "••••••••"  # noqa: S105 - a mask marker, not a password

#: Substrings that mark a config field as secret-bearing. A heuristic, carried
#: over verbatim: a credential stored under a name with none of these ("sid")
#: is returned raw, exactly as today.
SECRET_HINTS: Final[tuple[str, ...]] = ("key", "secret", "token", "password")

# --- Authorization scopes (unchanged from the legacy router, spec 0077) ----------------
READ_SCOPE: Final[str] = "platform:read"
WRITE_SCOPE: Final[str] = "platform:write"

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
#: Never the config — it holds credentials (AGENTS.md §4). Ids and kind only.
LOG_CREATED: Final[str] = "Integration created (id %s, kind %s)"
LOG_UPDATED: Final[str] = "Integration %s updated"
LOG_DELETED: Final[str] = "Integration %s deleted"
