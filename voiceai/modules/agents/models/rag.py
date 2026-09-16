"""Knowledge-retrieval schema: vector stores, reranking, and the canonical RAG config.

Moved verbatim from ``voiceai/models.py`` lines 250-327 (spec 0002, step A2); only import
statements changed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class MongoDBProviderConfig(BaseModel):
    """Vector-store settings for a MongoDB Atlas Search backend."""

    connection_string: str | None = None
    db_name: str | None = None
    collection_name: str | None = None
    index_name: str | None = None
    llm_model: str | None = "gpt-3.5-turbo"
    embedding_model: str | None = "text-embedding-3-small"
    embedding_dimensions: int | None = 256


class RerankerConfig(BaseModel):
    """Configuration for document reranking in RAG systems."""

    enabled: bool = False
    model_type: str = "minilm-l6-v2"  # bge-base, bge-large, bge-multilingual, minilm-l6-v2
    candidate_count: int = 20  # How many candidates to retrieve before reranking
    final_count: int = 5  # Final number of results to return after reranking

    @field_validator("model_type")
    def validate_reranker_model(cls, value: str) -> str:
        """Reject a reranker model outside the supported set."""
        allowed_models = ["bge-base", "bge-large", "bge-multilingual", "minilm-l6-v2"]
        if value not in allowed_models:
            raise ValueError(f"Invalid reranker model: '{value}'. Supported models: {allowed_models}")
        return value

    @field_validator("candidate_count")
    def validate_candidate_count(cls, value: int) -> int:
        """Bound the pre-rerank candidate pool to 1-100."""
        if value < 1 or value > 100:
            raise ValueError("candidate_count must be between 1 and 100")
        return value

    @field_validator("final_count")
    def validate_final_count(cls, value: int) -> int:
        """Bound the post-rerank result count to 1-50."""
        if value < 1 or value > 50:
            raise ValueError("final_count must be between 1 and 50")
        return value


class LanceDBProviderConfig(BaseModel):
    """Vector-store settings for the LanceDB backend, tolerant of enrichment extras."""

    # extra="allow" keeps call-time enrichment fields (chunk_size, overlapping) that the
    # backend injects into provider_config before sending the config to the engine.
    model_config = {"extra": "allow"}

    vector_id: str | None = None
    vector_ids: list[str] | None = None
    similarity_top_k: int | None = 5
    score_threshold: float | None = 0.1
    reranker: RerankerConfig | None = RerankerConfig()  # Default to disabled reranker

    @model_validator(mode="after")
    def require_vector_identifier(self) -> LanceDBProviderConfig:
        """Demand at least one of ``vector_id``/``vector_ids``."""
        if not self.vector_id and not self.vector_ids:
            raise ValueError("Either vector_id or vector_ids must be provided")
        return self


class VectorStore(BaseModel):
    """A provider name plus its provider-specific vector-store configuration."""

    provider: str
    provider_config: LanceDBProviderConfig | MongoDBProviderConfig = Field(union_mode="left_to_right")


class UsedSource(BaseModel):
    """One retrieval source consulted during a call, populated server-side."""

    rag_id: str | None = None
    vector_id: str | None = None
    source: str | None = None


class RagConfig(BaseModel):
    """Canonical knowledge-base config shared by the knowledgebase agent, graph agents
    (global) and graph nodes. used_sources is populated server-side at call time.
    """

    # extra="allow" preserves server-injected enrichment keys and any node-level extras.
    model_config = {"extra": "allow"}

    vector_store: VectorStore
    similarity_top_k: int | None = None
    used_sources: list[UsedSource] | None = None
