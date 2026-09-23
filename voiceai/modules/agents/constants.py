"""Every literal the agents module uses (AGENTS.md rule 1b): keys, routes, files, env names.

These values pin the legacy contracts spec 0002 preserves verbatim: the bare-UUID redis key
scheme, the CWD-relative prompt directory, the quickstart JSON wire shapes, and the
`agent_type` dispatch table. Steps A2-A7 consume them; changing a value here is a behavior
change and needs its own spec.
"""

from __future__ import annotations

from typing import Final

from voiceai.enums import ReasoningEffort

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

# --- Brains: judgment-LLM contract shared by simple + knowledgebase (A6) ----------------
# TODO(spec-0004): the brains read these env vars directly (verbatim legacy behavior);
# they migrate to `core.environment` when the voice runtime composes the brains.
ENV_CHECK_FOR_COMPLETION_LLM: Final[str] = "CHECK_FOR_COMPLETION_LLM"
ENV_VOICEMAIL_DETECTION_LLM: Final[str] = "VOICEMAIL_DETECTION_LLM"
DEFAULT_VOICEMAIL_DETECTION_MODEL: Final[str] = "gpt-4.1-mini"
#: The name tag every brain inherits from `brains.base.BaseAgent`.
BASE_AGENT_NAME: Final[str] = "base-agent"
# LLM chat-message wire keys the judgment prompts are built from.
MESSAGE_ROLE_KEY: Final[str] = "role"
MESSAGE_CONTENT_KEY: Final[str] = "content"
SYSTEM_ROLE: Final[str] = "system"
USER_ROLE: Final[str] = "user"
# Judgment answers: any failure degrades to "keep talking" / "not a voicemail".
HANGUP_KEY: Final[str] = "hangup"
IS_VOICEMAIL_KEY: Final[str] = "is_voicemail"
NEGATIVE_ANSWER: Final[str] = "No"
LATENCY_MS_KEY: Final[str] = "latency_ms"
#: The user turn both voicemail checks send (legacy wording, byte-identical).
VOICEMAIL_USER_MESSAGE_TEMPLATE: Final[str] = "User message: {user_message}"

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
#: Engine-dispatch kinds the conversation-brain factory builds (spec 0015): the
#: task-level `llm_agent.agent_type` values `__get_agent_object` switches on.
#: `multiagent`/`llm_agent_graph` flow through separate builders, never the factory.
ENGINE_KINDS: Final[tuple[str, ...]] = (
    AGENT_TYPE_SIMPLE_LLM,
    AGENT_TYPE_GRAPH,
    AGENT_TYPE_KNOWLEDGEBASE,
)

# --- Error messages (client-visible; the 404 text matches the legacy detail) ------------
AGENT_NOT_FOUND_MESSAGE: Final[str] = "Agent not found"

# --- Graph brain (A7): shared dict keys of the census-verified engine seam --------------
# The graph agent reads its config, nodes and edges as free-form dicts; these keys are
# shared by two or more files under `brains/graph/` (file-unique keys stay file-local
# Finals in their one consumer, mirroring the A6 brains).
GRAPH_NODE_ID_KEY: Final[str] = "id"
GRAPH_NODES_KEY: Final[str] = "nodes"
GRAPH_EDGES_KEY: Final[str] = "edges"
GRAPH_TO_NODE_ID_KEY: Final[str] = "to_node_id"
GRAPH_CONDITION_KEY: Final[str] = "condition"
GRAPH_CONDITION_TYPE_KEY: Final[str] = "condition_type"
GRAPH_FUNCTION_DESCRIPTION_KEY: Final[str] = "function_description"
GRAPH_PROMPT_KEY: Final[str] = "prompt"
GRAPH_MODEL_KEY: Final[str] = "model"
GRAPH_PROVIDER_KEY: Final[str] = "provider"
GRAPH_LLM_PROVIDER_KEY: Final[str] = "llm_provider"
GRAPH_CUSTOM_PROVIDER: Final[str] = "custom"
GRAPH_PROVIDER_OPENAI: Final[str] = "openai"
GRAPH_PROVIDER_AZURE: Final[str] = "azure"
GRAPH_PROVIDER_GROQ: Final[str] = "groq"
GRAPH_API_VERSION_KEY: Final[str] = "api_version"
GRAPH_OVERFLOW_LLM_KEY: Final[str] = "overflow_llm"
GRAPH_SERVICE_TIER_KEY: Final[str] = "service_tier"

# --- Graph brain (A7): shared context_data keys -----------------------------------------
GRAPH_RECIPIENT_DATA_KEY: Final[str] = "recipient_data"
GRAPH_DETECTED_LANGUAGE_KEY: Final[str] = "detected_language"
GRAPH_TIMEZONE_KEY: Final[str] = "timezone"
GRAPH_LAST_EVENT_KEY: Final[str] = "_last_event"
#: A silence-triggered turn arrives as a user message with this prefix.
GRAPH_SILENCE_MESSAGE_PREFIX: Final[str] = "[silence]"

# --- Graph brain (A7): chat-message keys beyond the shared judgment set -----------------
ASSISTANT_ROLE: Final[str] = "assistant"
TOOL_ROLE: Final[str] = "tool"
TOOL_CALLS_KEY: Final[str] = "tool_calls"
TOOL_CALL_ID_KEY: Final[str] = "tool_call_id"

# --- Graph brain (A7): routing_info wire keys (engine-consumed telemetry, verbatim) -----
ROUTING_PREVIOUS_NODE_KEY: Final[str] = "previous_node"
ROUTING_CURRENT_NODE_KEY: Final[str] = "current_node"
ROUTING_TRANSITIONED_KEY: Final[str] = "transitioned"
ROUTING_TYPE_KEY: Final[str] = "routing_type"
ROUTING_MODEL_KEY: Final[str] = "routing_model"
ROUTING_PROVIDER_KEY: Final[str] = "routing_provider"
ROUTING_LATENCY_MS_KEY: Final[str] = "routing_latency_ms"
ROUTING_EXTRACTED_PARAMS_KEY: Final[str] = "extracted_params"
ROUTING_NODE_HISTORY_KEY: Final[str] = "node_history"
ROUTING_MESSAGES_KEY: Final[str] = "routing_messages"
ROUTING_TOOLS_KEY: Final[str] = "routing_tools"
ROUTING_REASONING_KEY: Final[str] = "reasoning"
ROUTING_EXPRESSION_KEY: Final[str] = "routing_expression"
ROUTING_CONFIDENCE_KEY: Final[str] = "confidence"
ROUTING_USAGE_KEY: Final[str] = "routing_usage"
ROUTING_NODE_TYPE_KEY: Final[str] = "node_type"
ROUTING_IS_SILENCE_TRIGGER_KEY: Final[str] = "is_silence_trigger"

# --- Graph brain (A7): OpenAI function-tool schema keys (shared traversal/prompts) ------
TOOL_TYPE_KEY: Final[str] = "type"
TOOL_FUNCTION_TYPE: Final[str] = "function"
TOOL_FUNCTION_KEY: Final[str] = "function"
TOOL_NAME_KEY: Final[str] = "name"
TOOL_DESCRIPTION_KEY: Final[str] = "description"
TOOL_PARAMETERS_KEY: Final[str] = "parameters"
TOOL_PROPERTIES_KEY: Final[str] = "properties"
TOOL_REQUIRED_KEY: Final[str] = "required"
TOOL_OBJECT_TYPE: Final[str] = "object"
TOOL_STRING_TYPE: Final[str] = "string"
TOOL_NUMBER_TYPE: Final[str] = "number"

#: The engine's per-turn correlation id inside meta_info.
SEQUENCE_ID_KEY: Final[str] = "sequence_id"

# --- Graph brain (A7): routing tool + model-name plumbing -------------------------------
#: The escape-hatch transition the routing LLM may call when no edge matches.
GRAPH_STAY_ON_CURRENT_NODE_FUNCTION: Final[str] = "stay_on_current_node"
#: `"azure/gpt-4o".split("/", 1)[-1]` — provider-prefixed model names carry this separator.
GRAPH_MODEL_NAME_SEPARATOR: Final[str] = "/"

# --- Graph brain (A7): routing_type values ----------------------------------------------
ROUTING_TYPE_DETERMINISTIC: Final[str] = "deterministic"
ROUTING_TYPE_LLM: Final[str] = "llm"
ROUTING_TYPE_HOLD: Final[str] = "hold"
ROUTING_TYPE_EVENT: Final[str] = "event"

# --- Reasoning-effort support map (models split, A2) ------------------------------------
# TODO(spec-0002): mirrors the legacy `voiceai.constants.MODEL_REASONING_EFFORT_MAP`
# verbatim (a test pins the equality) because the layer contract bans `voiceai.constants`
# imports from module code; the legacy copy retires when the llms/platform migration spec
# moves its remaining consumers.
MODEL_REASONING_EFFORT_MAP: Final[dict[str, list[ReasoningEffort]]] = {
    "gpt-5": [ReasoningEffort.MINIMAL, ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5-mini": [ReasoningEffort.MINIMAL, ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5-nano": [ReasoningEffort.MINIMAL, ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5-codex": [ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5-pro": [ReasoningEffort.HIGH],
    "gpt-5.1": [ReasoningEffort.NONE, ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5.1-codex": [ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5.1-codex-max": [
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-5.1-codex-mini": [ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5.2": [
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-5.4": [
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-5.4-mini": [ReasoningEffort.NONE, ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5.4-nano": [ReasoningEffort.NONE, ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH],
    "gpt-5.5": [
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-5.5-pro": [ReasoningEffort.MEDIUM, ReasoningEffort.HIGH, ReasoningEffort.XHIGH],
    "gpt-5.6-sol": [
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-5.6-terra": [
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-5.6-luna": [
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    # Realtime speech-to-speech. gpt-realtime-1.5 has no reasoning and is deliberately absent.
    "gpt-realtime-2": [
        ReasoningEffort.MINIMAL,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-realtime-2.1": [
        ReasoningEffort.MINIMAL,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
    "gpt-realtime-2.1-mini": [
        ReasoningEffort.MINIMAL,
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ],
}

# --- Runtime performance (spec 0012: enterprise hot-path budgets) -------------------------
#: Read-through TTL for CachedAgentReader (call-setup definition+prompt reads).
RUNTIME_CACHE_TTL_S: Final[float] = 60.0
#: Max concurrent extraction-LLM generations during agent seeding (service fan-out).
MAX_EXTRACTION_CONCURRENCY: Final[int] = 4
#: Per-judgment timeout for the concurrent judgment runner (turn liveness).
JUDGMENT_TIMEOUT_S: Final[float] = 8.0
#: Max entries of the opt-in per-agent RAG retrieval cache (KB brain).
RAG_CACHE_MAX_ENTRIES: Final[int] = 32
#: Rag-config key enabling the retrieval cache; 0/absent keeps it off (default).
RAG_CACHE_TTL_KEY: Final[str] = "cache_ttl_s"
