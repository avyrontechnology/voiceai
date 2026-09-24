"""Single registry of persistence literals: collection names and repository messages."""

from __future__ import annotations

from enum import Enum
from typing import Final


class Collections(str, Enum):
    """Canonical collection/table name for every persisted model (AGENTS.md rule 5).

    A repository that spells a collection name inline instead of taking a member of this
    enum is a violation: renaming a collection must be a one-line change here.
    """

    USERS = "users"
    AGENTS = "agents"
    EXECUTIONS = "executions"
    HEALTH_CHECKS = "health_checks"
    WALLETS = "wallets"
    LEDGER = "ledger"
    TALKO_PARTNERS = "talko_partners"
    #: Seed agent-template catalog (T5b: templates.py is the seed source).
    AGENT_TEMPLATES = "agent_templates"
    #: Auth collections (greenfield T0 registry; stores land in the auth turn T2).
    #: Sessions-refresh holds opaque rotating refresh tokens (TTL on `expires_at`);
    #: revoked-tokens holds access-token denylist entries (TTL = access TTL).
    SESSIONS = "sessions"
    INVITES = "invites"
    AUTH_EVENTS = "auth_events"
    API_KEYS = "api_keys"
    REVOKED_TOKENS = "revoked_tokens"
    #: Agent prompt blobs (greenfield T3 replaces CWD-relative `agent_data/` files).
    AGENT_PROMPTS = "agent_prompts"
    #: Provider catalog rows (spec 0022, M-catalog): system-tenant modality /
    #: provider / model documents the agent builder dropdowns read.
    PROVIDER_CATALOG = "provider_catalog"


# Repository error text and the detail keys attached to it. Messages stay generic on purpose:
# they travel to clients inside the error envelope (AGENTS.md §4, error opacity).
DOCUMENT_NOT_FOUND_MESSAGE: Final[str] = "Document not found"
DETAIL_COLLECTION: Final[str] = "collection"
DETAIL_ITEM_ID: Final[str] = "item_id"

#: Document field carrying the isolation boundary (spec 0020, M1b). Scoped
#: repositories and the backfill reference the field through this, never a literal.
TENANT_ID_FIELD: Final[str] = "tenant_id"

# Mongo driver timeouts (spec 0003): every operation is bounded (AGENTS.md §4 — a hung
# database must degrade, never wedge a loop). Wired at client construction so they cover
# all operations without per-call kwargs the driver may not accept.
MONGO_TIMEOUT_MS: Final[int] = 5000
MONGO_SERVER_SELECTION_TIMEOUT_MS: Final[int] = 5000
MONGO_CONNECT_TIMEOUT_MS: Final[int] = 5000
MONGO_SOCKET_TIMEOUT_MS: Final[int] = 20000
