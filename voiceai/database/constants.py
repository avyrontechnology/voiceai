"""Single source of collection/table names (Constitution V).

Every model ``Settings.name`` and every migration/seed/admin job references
these constants. String-literal collection names anywhere else are a gate
violation.
"""

from __future__ import annotations

COLLECTIONS: dict[str, str] = {
    "API_KEYS": "api_keys",
    "AUTH_EVENTS": "auth_events",
    "BATCHES": "batches",
    "EXECUTIONS": "executions",
    "GRAPHS": "graphs",
    "GRAPH_VERSIONS": "graph_versions",
    "INBOUND_CONFIGS": "inbound_configs",
    "INTEGRATIONS": "integrations",
    "INVITES": "invites",
    "KNOWLEDGE_BASES": "knowledge_bases",
    "LEDGER_ENTRIES": "ledger_entries",
    "ORGANIZATIONS": "organizations",
    "PHONE_NUMBERS": "phone_numbers",
    "SESSIONS": "sessions",
    "SUB_ACCOUNTS": "sub_accounts",
    "TOOLS": "tools",
    "USERS": "users",
    "VECTOR_STORES": "vector_stores",
    "VOICES": "voices",
    "WALLETS": "wallets",
    "WEBHOOKS": "webhooks",
    "WORKFLOWS": "workflows",
    "WORKFLOW_CAMPAIGNS": "workflow_campaigns",
    "WORKFLOW_RUNS": "workflow_runs",
    "WORKFLOW_VERSIONS": "workflow_versions",
}
