"""RAG for the graph brain: per-node/global configs and retrieval glue (spec 0002, A7).

Behavior-preserving verbatim move of `voiceai/agent_types/graph_agent.py` lines 183-254
(collection extraction, per-node and agent-level RAG-config initialization) plus the
retrieval block of `_build_messages` (lines 1268-1294), extracted here as
:func:`fetch_rag_message` so the RAG lookup lives with the RAG configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from voiceai.common.logger import get_logger
from voiceai.modules.agents.adapters.brains import RAGServiceClientSingleton
from voiceai.modules.agents.constants import (
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    SEQUENCE_ID_KEY,
    SYSTEM_ROLE,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only facade import (no runtime cycle)
    from voiceai.modules.agents.brains.graph import GraphAgent

__all__ = [
    "extract_rag_collections",
    "extract_similarity_top_k",
    "fetch_rag_message",
    "initialize_global_rag_config",
    "initialize_rag_configs",
]

logger = get_logger(MODULE_NAME)

# --- RAG config keys (the legacy dict contract, verbatim) -------------------------------
_RAG_CONFIG_KEY: Final[str] = "rag_config"
_PROVIDER_CONFIG_KEY: Final[str] = "provider_config"
_VECTOR_STORE_KEY: Final[str] = "vector_store"
_VECTOR_ID_KEY: Final[str] = "vector_id"
_VECTOR_IDS_KEY: Final[str] = "vector_ids"
_USED_SOURCES_KEY: Final[str] = "used_sources"
_COLLECTIONS_KEY: Final[str] = "collections"
_SIMILARITY_TOP_K_KEY: Final[str] = "similarity_top_k"
_DEFAULT_SIMILARITY_TOP_K: Final[int] = 10
_SIMILARITY_THRESHOLD: Final[float] = 0.0
_NODES_KEY: Final[str] = "nodes"
_NODE_ID_KEY: Final[str] = "id"

# --- meta_info rag_latency wire keys ----------------------------------------------------
_RAG_LATENCY_KEY: Final[str] = "rag_latency"
_TOTAL_QUERY_TIME_MS_KEY: Final[str] = "total_query_time_ms"
_SERVER_PROCESSING_TIME_MS_KEY: Final[str] = "server_processing_time_ms"
_COLLECTIONS_COUNT_KEY: Final[str] = "collections_count"
_RESULTS_COUNT_KEY: Final[str] = "results_count"


def extract_rag_collections(rag_config: dict) -> list[str]:
    """Extract collection/vector IDs from a rag_config dict, supporting all known formats."""
    collections = []
    legacy_pc_raw = rag_config.get(_PROVIDER_CONFIG_KEY)
    legacy_pc = legacy_pc_raw if isinstance(legacy_pc_raw, dict) else {}
    provider_config = (rag_config.get(_VECTOR_STORE_KEY) or {}).get(_PROVIDER_CONFIG_KEY) or {}
    # Prefer the first location that actually carries a vector id; a present-but-empty
    # field (e.g. top-level "vector_ids": []) must not shadow a valid vector_store.
    if isinstance(rag_config.get(_VECTOR_IDS_KEY), list) and rag_config[_VECTOR_IDS_KEY]:
        collections.extend(rag_config[_VECTOR_IDS_KEY])
    elif rag_config.get(_VECTOR_ID_KEY):
        collections.append(rag_config[_VECTOR_ID_KEY])
    elif legacy_pc.get(_VECTOR_ID_KEY):
        collections.append(legacy_pc[_VECTOR_ID_KEY])
    elif isinstance(provider_config.get(_VECTOR_IDS_KEY), list) and provider_config[_VECTOR_IDS_KEY]:
        collections.extend(provider_config[_VECTOR_IDS_KEY])
    elif provider_config.get(_VECTOR_ID_KEY):
        collections.append(provider_config[_VECTOR_ID_KEY])
    return [c for c in collections if c]


def extract_similarity_top_k(rag_config: dict, default: int = _DEFAULT_SIMILARITY_TOP_K) -> int:
    """The similarity_top_k for a rag_config, from either config location."""
    provider_config = (rag_config.get(_VECTOR_STORE_KEY) or {}).get(_PROVIDER_CONFIG_KEY) or {}
    return rag_config.get(_SIMILARITY_TOP_K_KEY) or provider_config.get(_SIMILARITY_TOP_K_KEY) or default


def initialize_rag_configs(agent: GraphAgent) -> dict[str, dict]:
    """Initialize RAG configurations for each node."""
    rag_configs = {}
    for node in agent.config.get(_NODES_KEY, []):
        rag_config = node.get(_RAG_CONFIG_KEY)
        if not rag_config:
            continue

        collections = agent._extract_rag_collections(rag_config)
        # Nodes without resolvable collections fall back to the global rag_config
        if not collections:
            continue

        rag_configs[node[_NODE_ID_KEY]] = {
            _COLLECTIONS_KEY: collections,
            _SIMILARITY_TOP_K_KEY: agent._extract_similarity_top_k(rag_config),
        }

        logger.info(f"Initialized RAG config for node {node[_NODE_ID_KEY]} with collections: {collections}")

    return rag_configs


def initialize_global_rag_config(agent: GraphAgent) -> dict:
    """Initialize the agent-level RAG config (same shape as KnowledgeBaseAgent's rag_config)."""
    rag_config = agent.config.get(_RAG_CONFIG_KEY)
    if not rag_config:
        return {}

    collections = []
    used_sources = rag_config.get(_USED_SOURCES_KEY)
    if used_sources:
        collections = [
            source[_VECTOR_ID_KEY] for source in used_sources if isinstance(source, dict) and source.get(_VECTOR_ID_KEY)
        ]
    if not collections:
        collections = agent._extract_rag_collections(rag_config)

    if not collections:
        logger.warning("Global rag_config present but no collections resolved")
        return {}

    logger.info(f"Initialized global RAG config with collections: {collections}")
    return {
        _COLLECTIONS_KEY: collections,
        _SIMILARITY_TOP_K_KEY: agent._extract_similarity_top_k(rag_config),
        _USED_SOURCES_KEY: used_sources or [],
    }


async def fetch_rag_message(
    agent: GraphAgent, history: list[dict], meta_info: dict | None = None
) -> dict[str, Any] | None:
    """Query the RAG service for the latest user message and build the trailing message.

    Verbatim extraction of the retrieval block of the monolith's ``_build_messages``
    (lines 1268-1294): the node's RAG config (or the global fallback) drives one query,
    latency lands in ``meta_info["rag_latency"]``, and any failure is swallowed after
    logging — the turn proceeds without knowledge-base context.

    Args:
        agent: The composing facade (RAG configs and server URL).
        history: The conversation so far; the last message drives the query.
        meta_info: Engine call metadata, mutated with the retrieval latency.

    Returns:
        The trailing system message carrying the retrieved context, or None.
    """
    rag_message = None
    rag_config = agent.rag_configs.get(agent.current_node_id) or agent.global_rag_config
    if rag_config and rag_config.get(_COLLECTIONS_KEY):
        try:
            client = await RAGServiceClientSingleton.get_client(agent.rag_server_url)
            latest_message = history[-1][MESSAGE_CONTENT_KEY] if history else ""
            rag_response = await client.query_for_conversation(
                query=latest_message,
                collections=rag_config[_COLLECTIONS_KEY],
                max_results=rag_config.get(_SIMILARITY_TOP_K_KEY, _DEFAULT_SIMILARITY_TOP_K),
                similarity_threshold=_SIMILARITY_THRESHOLD,
            )
            if meta_info is not None:
                meta_info[_RAG_LATENCY_KEY] = {
                    SEQUENCE_ID_KEY: meta_info.get(SEQUENCE_ID_KEY),
                    _TOTAL_QUERY_TIME_MS_KEY: rag_response.total_query_time_ms,
                    _SERVER_PROCESSING_TIME_MS_KEY: rag_response.server_processing_time_ms,
                    _COLLECTIONS_COUNT_KEY: len(rag_config[_COLLECTIONS_KEY]),
                    _RESULTS_COUNT_KEY: rag_response.total_results,
                }
            if rag_response.contexts:
                rag_context = await client.format_context_for_prompt(rag_response.contexts)
                rag_message = {
                    MESSAGE_ROLE_KEY: SYSTEM_ROLE,
                    MESSAGE_CONTENT_KEY: (
                        f"Knowledge base for the latest user message:\n{rag_context}\n\nUse this information naturally."
                    ),
                }
        except Exception as e:  # legacy-parity(spec-0002): retrieval failures never break the turn
            logger.error(f"RAG error for node {agent.current_node_id}: {e}")
    return rag_message
