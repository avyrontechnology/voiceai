"""The knowledge-based (RAG) conversational brain (spec 0002, A6).

Behavior-preserving verbatim move of `voiceai/agent_types/knowledgebase_agent.py`.
This agent fetches relevant context from a knowledge base before responding, supports
function calling (API tools) just like the simple LLM agent, and supports hangup and
voicemail detection. Per the A6 step definition, ``RAG_SERVER_URL`` becomes a
constructor parameter with the legacy environment fallback — the engine's
``os.environ["RAG_SERVER_URL"]`` side-channel write (task_manager 1997/2050) stays the
composition path until spec 0004 passes the URL explicitly.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator
from typing import Any, Final

from voiceai.common.logger import get_logger
from voiceai.llms.openai_llm import OpenAiLLM
from voiceai.llms.types import LatencyData, LLMStreamChunk
from voiceai.modules.agents.adapters.brains import (
    SUPPORTED_LLM_PROVIDERS,
    VOICEMAIL_DETECTION_PROMPT,
    RAGServiceClientSingleton,
    format_messages,
    guard_llm_base_url,
    now_ms,
)
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.constants import (
    DEFAULT_RAG_SERVER_URL,
    DEFAULT_VOICEMAIL_DETECTION_MODEL,
    ENV_CHECK_FOR_COMPLETION_LLM,
    ENV_RAG_SERVER_URL,
    ENV_VOICEMAIL_DETECTION_LLM,
    HANGUP_KEY,
    IS_VOICEMAIL_KEY,
    LATENCY_MS_KEY,
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    NEGATIVE_ANSWER,
    SYSTEM_ROLE,
    USER_ROLE,
    VOICEMAIL_USER_MESSAGE_TEMPLATE,
)

__all__ = ["KnowledgeBaseAgent"]

logger = get_logger(MODULE_NAME)

#: Milliseconds per second — the legacy latency arithmetic, promoted from the inline 1000.
_MS_PER_SECOND: Final = 1000

# --- Config keys and defaults (the legacy dict contract, verbatim) ----------------------
_AGENT_INFORMATION_KEY: Final[str] = "agent_information"
_DEFAULT_AGENT_INFORMATION: Final[str] = "Knowledge-based AI assistant"
_CONTEXT_DATA_KEY: Final[str] = "context_data"
_MODEL_KEY: Final[str] = "model"
_DEFAULT_MODEL: Final[str] = "gpt-4o"
_PROVIDER_KEY: Final[str] = "provider"
_LLM_PROVIDER_KEY: Final[str] = "llm_provider"
_DEFAULT_PROVIDER: Final[str] = "openai"
_CUSTOM_PROVIDER: Final[str] = "custom"
_TEMPERATURE_KEY: Final[str] = "temperature"
_DEFAULT_TEMPERATURE: Final[float] = 0.7
_MAX_TOKENS_KEY: Final[str] = "max_tokens"
_DEFAULT_MAX_TOKENS: Final[int] = 150
_BASE_URL_KEY: Final[str] = "base_url"
_PROMPT_KEY: Final[str] = "prompt"
_ENV_OPENAI_API_KEY: Final[str] = "OPENAI_API_KEY"
#: Optional LLM kwargs forwarded ONLY when truthy (# legacy-parity: `.get(key, None)`
#: drops falsy values such as `buffer_size=0`).
_OPTIONAL_LLM_KEYS: Final[tuple[str, ...]] = (
    "llm_key",
    _BASE_URL_KEY,
    "api_version",
    "language",
    "api_tools",
    "buffer_size",
    "reasoning_effort",
    "verbosity",
    "reasoning_summary",
    "service_tier",
    "use_responses_api",
    "compact_threshold",
    "overflow_llm",
)

# --- RAG config keys and defaults -------------------------------------------------------
_RAG_CONFIG_KEY: Final[str] = "rag_config"
_USED_SOURCES_KEY: Final[str] = "used_sources"
_VECTOR_STORE_KEY: Final[str] = "vector_store"
_PROVIDER_CONFIG_KEY: Final[str] = "provider_config"
_VECTOR_ID_KEY: Final[str] = "vector_id"
_VECTOR_IDS_KEY: Final[str] = "vector_ids"
_RAG_ID_KEY: Final[str] = "rag_id"
_SOURCE_KEY: Final[str] = "source"
_COLLECTIONS_KEY: Final[str] = "collections"
_COLLECTION_ID_KEY: Final[str] = "collection_id"
_SIMILARITY_TOP_K_KEY: Final[str] = "similarity_top_k"
_DEFAULT_SIMILARITY_TOP_K: Final[int] = 10
_SIMILARITY_THRESHOLD: Final[float] = 0.0
_MAX_HISTORY_MESSAGES: Final[int] = 50

# --- Retrieval-metadata wire keys -------------------------------------------------------
_STATUS_KEY: Final[str] = "status"
_STATUS_ERROR: Final[str] = "error"
_STATUS_SUCCESS: Final[str] = "success"
_MESSAGE_KEY: Final[str] = "message"
_LATENCY_KEY: Final[str] = "latency"
_TEXT_KEY: Final[str] = "text"
_SCORE_KEY: Final[str] = "score"
_RETRIEVED_SOURCES_KEY: Final[str] = "retrieved_sources"
_CONTEXTS_KEY: Final[str] = "contexts"
_TOTAL_QUERY_TIME_MS_KEY: Final[str] = "total_query_time_ms"
_SERVER_PROCESSING_TIME_MS_KEY: Final[str] = "server_processing_time_ms"
_COLLECTIONS_COUNT_KEY: Final[str] = "collections_count"
_RESULTS_COUNT_KEY: Final[str] = "results_count"
_NO_KB_CONFIGURED_MESSAGE: Final[str] = "No knowledgebases configured"
_NO_CONTEXTS_MESSAGE: Final[str] = "No knowledgebase contexts found"
_INTERNAL_ERROR_MESSAGE: Final[str] = "Internal Service Error"

# --- meta_info wire keys ----------------------------------------------------------------
_LLM_METADATA_KEY: Final[str] = "llm_metadata"
_RAG_INFO_KEY: Final[str] = "rag_info"
_ALL_SOURCES_KEY: Final[str] = "all_sources"
_CONTEXT_RETRIEVAL_KEY: Final[str] = "context_retrieval"
_RAG_LATENCY_KEY: Final[str] = "rag_latency"
_SEQUENCE_ID_KEY: Final[str] = "sequence_id"
_MESSAGES_KEY: Final[str] = "messages"
_META_INFO_KEY: Final[str] = "meta_info"
_SYNTHESIZE_KEY: Final[str] = "synthesize"

#: The JSON-format instruction appended to the voicemail prompt (whitespace verbatim).
_VOICEMAIL_JSON_INSTRUCTION: Final[str] = """
                    Respond only in this JSON format:
                    {
                      "is_voicemail": "Yes" or "No"
                    }
                """


class KnowledgeBaseAgent(BaseAgent):
    """Knowledge-based conversational agent with RAG (Retrieval Augmented Generation) support.

    This agent:
    - Fetches relevant context from a knowledge base before responding
    - Supports function calling (API tools) just like the simple LLM agent
    - Supports hangup detection via check_for_completion()

    Args:
        config: The agent's free-form dict config (the census-verified engine seam).
        rag_server_url: Explicit RAG endpoint (A6 constructor parameter). Falls back to
            the ``RAG_SERVER_URL`` environment side-channel the engine writes, then to
            the legacy localhost default.
    """

    def __init__(self, config: dict[str, Any], rag_server_url: str | None = None) -> None:
        super().__init__()
        self.config = config
        self.agent_information = self.config.get(_AGENT_INFORMATION_KEY, _DEFAULT_AGENT_INFORMATION)
        self.context_data = self.config.get(_CONTEXT_DATA_KEY, {})
        self.llm_model = self.config.get(_MODEL_KEY, _DEFAULT_MODEL)
        self._base_url_validated = False

        # Main LLM for conversation
        self.llm = self._initialize_llm()

        # Separate LLM for checking if call should end
        # TODO(spec-0004): direct env reads are verbatim legacy behavior; they move to
        # `core.environment` when the voice runtime composes the judgment models.
        self.conversation_completion_llm = OpenAiLLM(model=os.getenv(ENV_CHECK_FOR_COMPLETION_LLM, self.llm_model))
        self.voicemail_llm = OpenAiLLM(model=os.getenv(ENV_VOICEMAIL_DETECTION_LLM, DEFAULT_VOICEMAIL_DETECTION_MODEL))
        # RAG configuration
        self.rag_config = self._initialize_rag_config()
        # TODO(spec-0004): the engine's `os.environ["RAG_SERVER_URL"]` write stays the
        # side-channel until composition passes the URL explicitly.
        env_rag_server_url: str = os.getenv(ENV_RAG_SERVER_URL, DEFAULT_RAG_SERVER_URL)
        self.rag_server_url: str = rag_server_url or env_rag_server_url

        logger.info(f"KnowledgeBaseAgent initialized with RAG collections: {self.rag_config.get(_COLLECTIONS_KEY, [])}")

    def _initialize_llm(self) -> Any:  # why: answers a legacy provider class instance (duck-typed)
        """Initialize the LLM instance with all necessary config (including api_tools for function calling)."""
        try:
            provider = self.config.get(_PROVIDER_KEY) or self.config.get(_LLM_PROVIDER_KEY, _DEFAULT_PROVIDER)

            if provider not in SUPPORTED_LLM_PROVIDERS:
                logger.warning(f"Unknown provider: {provider}, using openai")
                provider = _DEFAULT_PROVIDER

            llm_kwargs: dict[str, Any] = {
                _MODEL_KEY: self.llm_model,
                _TEMPERATURE_KEY: self.config.get(_TEMPERATURE_KEY, _DEFAULT_TEMPERATURE),
                _MAX_TOKENS_KEY: self.config.get(_MAX_TOKENS_KEY, _DEFAULT_MAX_TOKENS),
                _PROVIDER_KEY: provider,
            }

            for key in _OPTIONAL_LLM_KEYS:
                # legacy-parity(spec-0002): truthiness (not presence) gates the passthrough.
                if self.config.get(key, None):
                    llm_kwargs[key] = self.config[key]

            llm_class = SUPPORTED_LLM_PROVIDERS[provider]
            return llm_class(**llm_kwargs)

        except Exception as e:  # legacy-parity(spec-0002): any factory failure falls back
            logger.error(f"Failed to create LLM: {e}, falling back to basic OpenAI")
            from openai import OpenAI  # legacy-parity: call-time import, verbatim

            return OpenAI(api_key=os.getenv(_ENV_OPENAI_API_KEY))

    def _initialize_rag_config(self) -> dict[str, Any]:
        """Initialize RAG configuration from the provided config."""
        rag_config = self.config.get(_RAG_CONFIG_KEY, {})

        if not rag_config:
            logger.warning("No RAG config provided")
            return {}

        collections: list[str] = []
        used_sources = rag_config.get(_USED_SOURCES_KEY, None)

        if _VECTOR_STORE_KEY in rag_config:
            provider_config = rag_config[_VECTOR_STORE_KEY].get(_PROVIDER_CONFIG_KEY, {})

            if used_sources:
                for source in used_sources:
                    vector_id = source.get(_VECTOR_ID_KEY)
                    if vector_id:
                        collections.append(vector_id)

            else:
                # Support both formats: vector_ids (list) and vector_id (single)
                vector_ids = provider_config.get(_VECTOR_IDS_KEY)
                if vector_ids and isinstance(vector_ids, list):
                    collections.extend(vector_ids)
                elif vector_id := provider_config.get(_VECTOR_ID_KEY):
                    collections.append(vector_id)
                else:
                    logger.error("No vector_id or vector_ids found in rag_config")
        else:
            logger.error("No vector_store in rag_config")

        return {
            _COLLECTIONS_KEY: collections,
            _SIMILARITY_TOP_K_KEY: rag_config.get(_SIMILARITY_TOP_K_KEY, _DEFAULT_SIMILARITY_TOP_K),
            _USED_SOURCES_KEY: used_sources,
        }

    async def check_for_completion(
        self,
        messages: list[dict[str, Any]],  # why: free-form legacy chat messages
        check_for_completion_prompt: str,
        meta_info: dict[str, Any] | None = None,  # why: the engine's free-form call metadata
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Check if the conversation should end (used for auto-hangup feature).

        Args:
            messages: Conversation history, formatted into the user turn.
            check_for_completion_prompt: The system prompt driving the judgment.
            meta_info: Engine call metadata forwarded to the LLM.

        Returns:
            ``(judgment, metadata)`` — on failure ``({"hangup": "No"}, {})``.
        """
        try:
            prompt = [
                {MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: check_for_completion_prompt},
                {MESSAGE_ROLE_KEY: USER_ROLE, MESSAGE_CONTENT_KEY: format_messages(messages)},
            ]
            start_time = time.time()
            response, metadata = await self.conversation_completion_llm.generate(
                prompt, request_json=True, ret_metadata=True, meta_info=meta_info
            )
            latency_ms = (time.time() - start_time) * _MS_PER_SECOND

            result = json.loads(response)
            metadata[LATENCY_MS_KEY] = latency_ms
            return result, metadata
        except Exception as e:  # legacy-parity(spec-0002): any failure means "keep talking"
            logger.error(f"check_for_completion error: {e}")
            return {HANGUP_KEY: NEGATIVE_ANSWER}, {}

    async def check_for_voicemail(
        self,
        user_message: str,
        voicemail_detection_prompt: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Check if the user message indicates a voicemail system.

        Args:
            user_message: The transcribed message from the user.
            voicemail_detection_prompt: Custom prompt for voicemail detection (optional).

        Returns:
            ``(judgment, metadata)`` with ``latency_ms`` added — on failure
            ``({"is_voicemail": "No"}, {})``.
        """
        try:
            detection_prompt = voicemail_detection_prompt or VOICEMAIL_DETECTION_PROMPT
            prompt = [
                {
                    MESSAGE_ROLE_KEY: SYSTEM_ROLE,
                    MESSAGE_CONTENT_KEY: detection_prompt + _VOICEMAIL_JSON_INSTRUCTION,
                },
                {
                    MESSAGE_ROLE_KEY: USER_ROLE,
                    MESSAGE_CONTENT_KEY: VOICEMAIL_USER_MESSAGE_TEMPLATE.format(user_message=user_message),
                },
            ]

            start_time = time.time()
            response, metadata = await self.voicemail_llm.generate(prompt, request_json=True, ret_metadata=True)
            latency_ms = (time.time() - start_time) * _MS_PER_SECOND

            result = json.loads(response)
            metadata[LATENCY_MS_KEY] = latency_ms
            return result, metadata
        except Exception as e:  # legacy-parity(spec-0002): any failure means "not a voicemail"
            logger.error(f"check_for_voicemail exception: {e}")
            return {IS_VOICEMAIL_KEY: NEGATIVE_ANSWER}, {}

    async def _add_rag_context(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Add relevant knowledge base context to the messages.

        Returns the original messages if RAG is not configured or fails.

        Args:
            messages: The conversation so far; the last user turn drives the query.

        Returns:
            ``(messages, retrieval_metadata)`` — the messages enhanced with a RAG system
            prompt on success, untouched otherwise.
        """
        if not self.rag_config.get(_COLLECTIONS_KEY):
            return messages, {_STATUS_KEY: _STATUS_ERROR, _MESSAGE_KEY: _NO_KB_CONFIGURED_MESSAGE}

        try:
            client = await RAGServiceClientSingleton.get_client(self.rag_server_url)

            latest_message = messages[-1][MESSAGE_CONTENT_KEY] if messages else ""

            rag_response = await client.query_for_conversation(
                query=latest_message,
                collections=self.rag_config[_COLLECTIONS_KEY],
                max_results=self.rag_config.get(_SIMILARITY_TOP_K_KEY, _DEFAULT_SIMILARITY_TOP_K),
                similarity_threshold=_SIMILARITY_THRESHOLD,
            )

            # Capture latency data from RAG response
            rag_latency_data = {
                _TOTAL_QUERY_TIME_MS_KEY: rag_response.total_query_time_ms,
                _SERVER_PROCESSING_TIME_MS_KEY: rag_response.server_processing_time_ms,
                _COLLECTIONS_COUNT_KEY: len(self.rag_config[_COLLECTIONS_KEY]),
                _RESULTS_COUNT_KEY: rag_response.total_results,
            }

            if not rag_response.contexts:
                return messages, {
                    _STATUS_KEY: _STATUS_ERROR,
                    _MESSAGE_KEY: _NO_CONTEXTS_MESSAGE,
                    _LATENCY_KEY: rag_latency_data,
                }

            used_vector_ids: set[str] = set()
            for context in rag_response.contexts:
                metadata = context.metadata
                vector_id = metadata.get(_COLLECTION_ID_KEY, None)
                if vector_id:
                    used_vector_ids.add(vector_id)

            retrieved_sources = []
            # legacy-parity(spec-0002): `used_sources` is stored as None for the
            # vector_ids config format, and iterating it raises here — so even a
            # successful retrieval degrades to the opaque internal error below (a
            # characterization test pins this).
            for source in self.rag_config.get(_USED_SOURCES_KEY, []):
                vector_id = source.get(_VECTOR_ID_KEY)
                if vector_id in used_vector_ids:
                    retrieved_sources.append(source)

            # Build a mapping from vector_id to source info
            # why: a context without a collection_id looks up None here — the legacy
            # dict tolerates it and answers the empty source, so the key stays open.
            vector_id_to_source: dict[Any, dict[str, Any]] = {}
            for source in self.rag_config.get(_USED_SOURCES_KEY, []):
                vid = source.get(_VECTOR_ID_KEY)
                if vid:
                    vector_id_to_source[vid] = source

            # Build contexts list with text and source info
            retrieved_contexts = []
            for context in rag_response.contexts:
                vector_id = context.metadata.get(_COLLECTION_ID_KEY, None)
                source_info = vector_id_to_source.get(vector_id, {})
                context_entry = {
                    _TEXT_KEY: context.text,
                    _SCORE_KEY: context.score,
                    _VECTOR_ID_KEY: source_info.get(_VECTOR_ID_KEY),
                    _RAG_ID_KEY: source_info.get(_RAG_ID_KEY),
                    _SOURCE_KEY: source_info.get(_SOURCE_KEY),
                }
                retrieved_contexts.append(context_entry)

            logger.info(
                f"RAG: Found {rag_response.total_results} contexts, top score: {rag_response.contexts[0].score:.3f}"
            )

            rag_context = await client.format_context_for_prompt(rag_response.contexts)

            if messages and messages[0].get(MESSAGE_ROLE_KEY) == SYSTEM_ROLE:
                system_prompt = messages[0][MESSAGE_CONTENT_KEY]
                other_messages = messages[1:]
            else:
                system_prompt = self.config.get(_PROMPT_KEY, f"You are {self.agent_information}.")
                other_messages = messages

            # Add RAG context to system prompt (legacy wording, byte-identical)
            enhanced_system_prompt = f"""{system_prompt}

You have access to relevant information from the knowledge base:

{rag_context}

Use this information naturally when it helps answer the user's questions. Don't force references if not relevant to the conversation."""  # noqa: E501 — legacy prompt text, byte-identical

            # Build final messages
            final_messages = [
                {MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: enhanced_system_prompt}
            ] + other_messages

            # Limit history size
            max_messages = _MAX_HISTORY_MESSAGES
            if len(final_messages) > max_messages:
                final_messages = [final_messages[0]] + final_messages[-(max_messages - 1) :]

            return final_messages, {
                _STATUS_KEY: _STATUS_SUCCESS,
                _RETRIEVED_SOURCES_KEY: retrieved_sources,
                _CONTEXTS_KEY: retrieved_contexts,
                _LATENCY_KEY: rag_latency_data,
            }

        except asyncio.TimeoutError:
            logger.error("RAG service timeout")
            return messages, {_STATUS_KEY: _STATUS_ERROR, _MESSAGE_KEY: _INTERNAL_ERROR_MESSAGE}
        except Exception as e:  # legacy-parity(spec-0002): retrieval failures stay opaque
            logger.error(f"RAG error: {e}")
            return messages, {_STATUS_KEY: _STATUS_ERROR, _MESSAGE_KEY: _INTERNAL_ERROR_MESSAGE}

    async def generate(
        self,
        message: list[dict[str, Any]],  # why: free-form legacy chat history
        **kwargs: Any,  # why: the legacy stream contract passes synthesize/meta_info loosely
    ) -> AsyncGenerator[Any, None]:  # why: yields a messages signal, provider chunks, or an error chunk
        """Generate a streaming response with RAG context.

        Args:
            message: The conversation so far.
            **kwargs: ``meta_info`` (mutated with RAG metadata) and ``synthesize``.

        Yields:
            First ``{"messages": <enhanced messages>}``, then the provider stream's
            chunks; on failure a final ``LLMStreamChunk`` carrying the error text.
        """
        # why: the engine always supplies meta_info; a None crashing on the writes
        # below is verbatim legacy behavior, so the type stays open.
        meta_info: Any = kwargs.get(_META_INFO_KEY)
        synthesize = kwargs.get(_SYNTHESIZE_KEY, True)
        start_time = now_ms()

        meta_info[_LLM_METADATA_KEY] = meta_info.get(_LLM_METADATA_KEY, {})
        meta_info[_LLM_METADATA_KEY][_RAG_INFO_KEY] = {}
        meta_info[_LLM_METADATA_KEY][_RAG_INFO_KEY][_ALL_SOURCES_KEY] = self.rag_config.get(_USED_SOURCES_KEY, [])

        # Ahead of the try so a blocked endpoint ends the call instead of being spoken.
        provider = self.config.get(_PROVIDER_KEY) or self.config.get(_LLM_PROVIDER_KEY)
        base_url = self.config.get(_BASE_URL_KEY) if provider == _CUSTOM_PROVIDER else None
        if base_url and not self._base_url_validated:
            await guard_llm_base_url(base_url)
            self._base_url_validated = True

        try:
            messages_with_context, metadata = await self._add_rag_context(message)

            meta_info[_LLM_METADATA_KEY][_RAG_INFO_KEY][_CONTEXT_RETRIEVAL_KEY] = metadata

            # Set rag_latency for task_manager to collect
            if metadata.get(_LATENCY_KEY):
                meta_info[_RAG_LATENCY_KEY] = {
                    _SEQUENCE_ID_KEY: meta_info.get(_SEQUENCE_ID_KEY),
                    **metadata[_LATENCY_KEY],
                }

            yield {_MESSAGES_KEY: messages_with_context}

            async for chunk in self.llm.generate_stream(
                messages_with_context, synthesize=synthesize, meta_info=meta_info
            ):
                yield chunk

        except Exception as e:  # legacy-parity(spec-0002): the error text is spoken downstream
            logger.error(f"generate() error: {e}")
            latency_data = LatencyData(
                sequence_id=meta_info.get(_SEQUENCE_ID_KEY) if meta_info else None,
                first_token_latency_ms=0,
                total_stream_duration_ms=now_ms() - start_time,
            )
            yield LLMStreamChunk(data=f"An error occurred: {str(e)}", end_of_stream=True, latency=latency_data)
