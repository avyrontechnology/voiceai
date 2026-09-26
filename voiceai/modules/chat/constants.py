"""Every literal the chat module uses (AGENTS.md rule 1b)."""

from __future__ import annotations

from typing import Final, Literal

#: Module name for the logger, router tags and registry entry.
MODULE_NAME: Final[str] = "chat"

#: Router tag for the chat endpoints.
CHAT_TAG: Final[str] = "Chat"

#: Route paths (mounted under the API prefix by the app factory).
CHAT_POST_PATH: Final[str] = "/chat/{agent_id}"
CHAT_SESSIONS_PATH: Final[str] = "/chat/sessions"

#: History bound: a session never stores more than this many turns (oldest drop on write).
MAX_HISTORY: Final[int] = 100

#: How many trailing turns ride each LLM call (full sessions persist, windows infer).
HISTORY_WINDOW: Final[int] = 20

#: Session id prefix (`ses_` + 12 hex chars via `common.ids.new_id`).
SESSION_ID_PREFIX: Final[str] = "ses"

#: Turn roles stored in a session (the LLM system prompt rides the wire only, never the store).
ChatRole = Literal["user", "assistant"]

#: The channel a definitions record must serve for the chat path to accept it.
CHAT_CHANNEL: Final[str] = "chat"

#: Channels assumed when an agent record carries no explicit list (voice precedent:
#: absence means the legacy voice-only agent, which is a 400 here, not a 404).
DEFAULT_CHANNELS: Final[tuple[str, ...]] = ("voice",)

#: SSE transport: fixed-size reply slices stream as `data:` frames plus terminal `[DONE]`.
SSE_MEDIA_TYPE: Final[str] = "text/event-stream"
SSE_DATA_PREFIX: Final[str] = "data:"
SSE_DONE_MARKER: Final[str] = "[DONE]"
SSE_CHUNK_SIZE: Final[int] = 120

#: LLM turn budget: the port owns its own bounds, this caps a hung runner (AGENTS.md §4).
CHAT_LLM_TIMEOUT_S: Final[float] = 30.0

#: Document field carrying the owning agent (repository filter, never user input).
AGENT_ID_FIELD: Final[str] = "agent_id"

#: Agent-config keys the service reads off the raw definition dicts (engine seam).
AGENT_CHANNELS_KEY: Final[str] = "channels"
AGENT_NAME_KEY: Final[str] = "agent_name"
AGENT_WELCOME_KEY: Final[str] = "agent_welcome_message"
TASKS_KEY: Final[str] = "tasks"
TOOLS_CONFIG_KEY: Final[str] = "tools_config"
LLM_AGENT_KEY: Final[str] = "llm_agent"
MODEL_KEY: Final[str] = "model"

#: LLM wire keys and roles (local copies — cross-module constant imports are banned).
MESSAGE_ROLE_KEY: Final[str] = "role"
MESSAGE_CONTENT_KEY: Final[str] = "content"
ROLE_SYSTEM: Final[str] = "system"
ROLE_USER: Final[ChatRole] = "user"
ROLE_ASSISTANT: Final[ChatRole] = "assistant"

#: Response header surfacing the resumed/minted session id beside the SSE frames,
#: so a client can continue the conversation (spec pins the service return, not
#: the transport — the header keeps every frame a plain-text slice).
SESSION_ID_HEADER: Final[str] = "x-session-id"

#: System prompt shaping: agent-name context plus the welcome message as greeting truth.
SYSTEM_PROMPT_TEMPLATE: Final[str] = "You are {agent_name}, a helpful AI assistant."
WELCOME_CONTEXT_TEMPLATE: Final[str] = " The agent introduces itself as: {welcome}"
DEFAULT_AGENT_NAME: Final[str] = "assistant"

#: Client-visible messages (identifiers only, never secrets or exception text).
AGENT_UNKNOWN_MESSAGE: Final[str] = "Agent not found"
SESSION_UNKNOWN_MESSAGE: Final[str] = "Chat session not found"
CHANNEL_MISMATCH_MESSAGE: Final[str] = "Agent does not serve the chat channel"
BLANK_CONTENT_MESSAGE: Final[str] = "Message must not be blank"
COMPLETION_FAILED_MESSAGE: Final[str] = "Chat completion failed"
COMPLETION_TIMEOUT_MESSAGE: Final[str] = "Chat completion timed out"
COMPLETION_UNWIRED_MESSAGE: Final[str] = "Chat completion runner is not wired"
AUTH_REQUIRED_MESSAGE: Final[str] = "Authentication required"
