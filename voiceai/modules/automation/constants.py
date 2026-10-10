"""Every literal the automation module uses: identity, routes, scopes, defaults."""

from __future__ import annotations

from typing import Final

from voiceai.database.constants import Collections

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "automation"
GRAPHS_ROUTE_PREFIX: Final[str] = "/graphs"
GRAPHS_TAG: Final[str] = "Graphs"

# --- Persistence -----------------------------------------------------------------------
#: Graph records. The member keeps its `PLATFORM_` prefix because the row name
#: predates this module; renaming it would orphan live graphs (spec 0078).
GRAPH_COLLECTION: Final[Collections] = Collections.PLATFORM_GRAPHS
#: Immutable snapshots of a graph, numbered per graph (spec 0078).
GRAPH_VERSION_COLLECTION: Final[Collections] = Collections.PLATFORM_GRAPH_VERSIONS

# --- Versioning -------------------------------------------------------------------------
#: The snapshot note written when a restore overwrites the live graph. Recorded
#: so the audit trail reads the same before and after the migration.
RESTORE_NOTE_TEMPLATE: Final[str] = "before restore of v{version_number}"

# --- Engine ----------------------------------------------------------------------------
#: Hard cap on the structural dry-run walk, so a cyclic graph cannot spin.
MAX_DRY_RUN_STEPS: Final[int] = 20

#: Engine evaluation tiers: expressions first, LLM intent next, unconditional last.
TIER_RANK: Final[dict[str, int]] = {"expression": 0, "llm": 1, "unconditional": 2, "event": 1}

#: Priority applied when an edge does not set its own.
DEFAULT_PRIORITY: Final[dict[str, int]] = {"expression": 0, "unconditional": 0, "llm": 100, "event": 100}

#: The agent type a deployed graph is registered as.
AGENT_TYPE_OTHER: Final[str] = "other"

# --- Authorization scopes (unchanged from the legacy router, spec 0078) ----------------
READ_SCOPE: Final[str] = "platform:read"
WRITE_SCOPE: Final[str] = "platform:write"
#: Deploy is the one graph route that is NOT `platform:write`; it moves verbatim.
DEPLOY_SCOPE: Final[str] = "agents:write"

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
#: `definition` is customer content, so only ids are ever logged.
LOG_CREATED: Final[str] = "Graph created (id %s)"
LOG_UPDATED: Final[str] = "Graph %s updated"
LOG_DELETED: Final[str] = "Graph %s deleted"
LOG_SNAPSHOT: Final[str] = "Graph %s snapshotted as v%s"
LOG_RESTORED: Final[str] = "Graph %s restored from v%s"
LOG_DEPLOYED: Final[str] = "Graph %s deployed as agent payload '%s'"
