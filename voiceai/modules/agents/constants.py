"""Every literal the agents module uses (AGENTS.md rule 1b): keys, routes, files, env names.

These values pin the legacy contracts spec 0002 preserves verbatim: the bare-UUID redis key
scheme, the CWD-relative prompt directory, the quickstart JSON wire shapes, and the
`agent_type` dispatch table. Steps A2-A7 consume them; changing a value here is a behavior
change and needs its own spec.
"""

from __future__ import annotations

from typing import Final

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "agents"

# --- Routes (relative to the app factory's API prefix; the controller lands in A4) ------
AGENT_PATH: Final[str] = "/agent"
AGENT_BY_ID_PATH: Final[str] = "/agent/{agent_id}"
AGENT_PROMPTS_PATH: Final[str] = "/agent/{agent_id}/prompts"
ALL_AGENTS_PATH: Final[str] = "/all"

# --- Redis key contract (bare-UUID scheme, preserved verbatim) --------------------------
# Agent configs live under BARE UUID keys in the shared redis; platform data always uses
# ":"-namespaced keys (plus index sets). The legacy directory scan is `KEYS *` with
# ":"-keys skipped BEFORE the GET — reading a namespaced set as a string raises WRONGTYPE.
# TODO(spec-0002): `KEYS *` is documented preserved debt for the platform-store spec; the
# repository (step A3) keeps it behind one documented method until that spec replaces it.
REDIS_KEY_NAMESPACE_SEPARATOR: Final[str] = ":"
REDIS_SCAN_ALL_PATTERN: Final[str] = "*"

# --- Agent record wire shape (quickstart JSON, byte-identical) --------------------------
AGENT_ID_KEY: Final[str] = "agent_id"
AGENT_DATA_KEY: Final[str] = "data"
AGENTS_KEY: Final[str] = "agents"
# A genuine agent record is a JSON object whose "tasks" value is a list; anything else on a
# bare key is skipped by the directory scan.
TASKS_KEY: Final[str] = "tasks"
STATE_KEY: Final[str] = "state"

# --- Assistant status injection (quickstart CRUD, preserved verbatim) -------------------
ASSISTANT_STATUS_KEY: Final[str] = "assistant_status"
ASSISTANT_STATUS_SEEDING: Final[str] = "seeding"
ASSISTANT_STATUS_UPDATED: Final[str] = "updated"

# --- CRUD response states (quickstart JSON, byte-identical) -----------------------------
AGENT_STATE_CREATED: Final[str] = "created"
AGENT_STATE_UPDATED: Final[str] = "updated"
AGENT_STATE_DELETED: Final[str] = "deleted"

# --- Prompt storage --------------------------------------------------------------------
# TODO(spec-0002): CWD-relative on purpose — the engine and the quickstart server resolve
# the same directory only because they share a working directory. Mirrors the legacy
# `voiceai.constants.PREPROCESS_DIR` (a test pins the equality); making it absolute is a
# deployment behavior change deferred to the cutover spec.
PREPROCESS_DIR: Final[str] = "agent_data"
CONVERSATION_DETAILS_FILE_NAME: Final[str] = "conversation_details.json"
# The storage key the prompt-file helpers receive: "<agent_id>/conversation_details.json".
PROMPT_FILE_KEY_TEMPLATE: Final[str] = "{agent_id}/" + CONVERSATION_DETAILS_FILE_NAME

# --- Extraction prompt generation (service lands in A4) ---------------------------------
TASK_TYPE_EXTRACTION: Final[str] = "extraction"
EXTRACTION_PROMPT_MAX_TOKENS: Final[int] = 2000
ENV_EXTRACTION_PROMPT_GENERATION_MODEL: Final[str] = "EXTRACTION_PROMPT_GENERATION_MODEL"

# --- RAG side-channel (brains land in A6/A7) --------------------------------------------
# TODO(spec-0004): the engine writes `os.environ["RAG_SERVER_URL"]` as a side-channel to
# the knowledgebase brain; the env write stays until composition passes the URL explicitly.
ENV_RAG_SERVER_URL: Final[str] = "RAG_SERVER_URL"
DEFAULT_RAG_SERVER_URL: Final[str] = "http://localhost:8000"

# --- agent_type dispatch (the LlmAgent validator's exact key set, moved in A2) ----------
AGENT_TYPE_SIMPLE_LLM: Final[str] = "simple_llm_agent"
AGENT_TYPE_GRAPH: Final[str] = "graph_agent"
AGENT_TYPE_LLM_GRAPH: Final[str] = "llm_agent_graph"
AGENT_TYPE_MULTIAGENT: Final[str] = "multiagent"
AGENT_TYPE_KNOWLEDGEBASE: Final[str] = "knowledgebase_agent"
AGENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        AGENT_TYPE_SIMPLE_LLM,
        AGENT_TYPE_GRAPH,
        AGENT_TYPE_LLM_GRAPH,
        AGENT_TYPE_MULTIAGENT,
        AGENT_TYPE_KNOWLEDGEBASE,
    }
)
# `AgentModel.agent_type` defaults to "other" — a top-level label, not a dispatch key.
DEFAULT_AGENT_TYPE: Final[str] = "other"

# --- Error messages (client-visible; the 404 text matches the legacy detail) ------------
AGENT_NOT_FOUND_MESSAGE: Final[str] = "Agent not found"
