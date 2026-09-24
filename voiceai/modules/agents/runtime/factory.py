"""Conversation-brain builder (spec 0015: rethink of the v1 registry).

v1 (spec 0012) keyed seven heterogeneous constructors by authoring-level strings
matching neither the engine vocabulary (`simple_llm_agent`, ...) nor the record
taxonomy (`other`, `voice`, ...) — and nothing used it. v2 models what the engine
actually does in `TaskManager.__get_agent_object`:

- dispatch on task-level `llm_agent.agent_type` (engine vocabulary only),
- assemble `injected_cfg` through ONE shared kwargs merge (graph and knowledgebase
  branches duplicated ~15 lines each),
- resolve the RAG URL (kwarg > env > default) with the pinned env side-channel write,
- construct through injectable constructors (tests/tenants substitute fakes).

Engine wiring (AGENTS.md §3.1 bridge 3): the factory INSTANCE crosses into legacy
via the `brain_factory` task kwarg, so `task_manager.py` gains zero imports —
receiving an injected object is not an import. Since spec 0024 (M3 cutover)
the factory path is the ONLY path: sessions without the kwarg fail fast naming
the spec. `tests/test_brain_factory_equivalence.py` pins the assembly.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol, cast, runtime_checkable

from voiceai.common.logger import get_logger
from voiceai.modules.agents.constants import (
    AGENT_TYPE_GRAPH,
    AGENT_TYPE_KNOWLEDGEBASE,
    AGENT_TYPE_SIMPLE_LLM,
    ENGINE_KINDS,
    MODULE_NAME,
)
from voiceai.modules.agents.errors import AgentsError

__all__ = [
    "BrainFactory",
    "BrainPort",
    "ENGINE_KINDS",
    "inject_shared_call_context",
    "resolve_kind",
    "resolve_rag_server_url",
]

logger = get_logger(MODULE_NAME)

#: Error text for an unresolvable brain kind (client-visible 500 envelope, error opacity §4).
_UNKNOWN_BRAIN_MESSAGE: Final[str] = "Unknown conversation brain type"
#: Default RAG endpoint when neither the call kwargs nor the environment names one.
_DEFAULT_RAG_SERVER_URL: Final[str] = "http://localhost:8000"
#: Env var naming the RAG endpoint (read here, written here — pinned side-channel).
_RAG_SERVER_URL_ENV: Final[str] = "RAG_SERVER_URL"

#: Call-kwarg keys merged into every graph/knowledgebase config, verbatim.
_SHARED_KWARG_KEYS: Final[tuple[str, ...]] = (
    "llm_key",
    "base_url",
    "api_version",
    "api_tools",
    "reasoning_effort",
    "reasoning_summary",
    "service_tier",
    "overflow_llm",
)
#: Graph-only call-kwarg keys, layered after the shared merge.
_GRAPH_KWARG_KEYS: Final[tuple[str, ...]] = (
    "routing_reasoning_effort",
    "routing_max_tokens",
    "aux_model",
    "aux_provider",
    "route_routing_to_conversation",
)
#: `llm_config` flags promoted into the injected config when truthy (legacy quirk).
_LLM_FLAG_KEYS: Final[tuple[str, ...]] = ("use_responses_api", "compact_threshold")

_LOG_SETTING_UP_GRAPH: Final[str] = "Setting up graph agent with rag-proxy-server support"
_LOG_SETTING_UP_KNOWLEDGE: Final[str] = "Setting up knowledge agent with rag-proxy-server support"
_LOG_GRAPH_CONFIG: Final[str] = "Graph agent config: %s"
_LOG_KNOWLEDGE_CONFIG: Final[str] = "Knowledge agent config: %s"
_LOG_RAG_URL: Final[str] = "RAG server URL: %s"
_LOG_GRAPH_CREATED: Final[str] = "Graph agent created with rag-proxy-server support"
_LOG_KNOWLEDGE_CREATED: Final[str] = "Knowledge agent created with rag-proxy-server support"


@runtime_checkable
class BrainPort(Protocol):
    """The structural surface the engine consumes from any brain (spec 0012).

    Deliberately the intersection every shipped brain already satisfies: stream
    tokens, answer the two side judgments. Plain dicts — the engine seam stays
    untyped, exactly as the ports module documents for the stores.
    """

    async def generate(
        self, history: list[dict[str, Any]], **kwargs: Any
    ) -> Any:  # why: provider streams yield heterogeneous chunks
        """Stream response tokens for ``history``."""
        ...

    async def check_for_completion(
        self,
        messages: list[dict[str, Any]],  # why: free-form legacy chat messages
        check_for_completion_prompt: str,
        meta_info: dict[str, Any] | None = None,  # why: engine call metadata
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Answer whether the conversation should end (fail-safe: keep talking)."""
        ...

    async def check_for_voicemail(
        self,
        user_message: str,
        voicemail_detection_prompt: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Answer whether the message indicates voicemail (fail-safe: not voicemail)."""
        ...


def resolve_kind(raw: str | None, *, valid: tuple[str, ...] = ENGINE_KINDS) -> str:
    """Resolve a raw task-level agent type to a buildable engine kind.

    Args:
        raw: The `llm_agent.agent_type` value (`None` when the key is absent).
        valid: Kinds the caller's registry serves (tenants extend via `register`).

    Returns:
        The validated kind unchanged.

    Raises:
        AgentsError: When `raw` is missing or unregistered — fail loud with the
            offending value and the valid set, instead of the legacy `raise f`
            TypeError deep in call setup.
    """
    if raw in valid:
        return str(raw)
    raise AgentsError(_UNKNOWN_BRAIN_MESSAGE, details={"agent_type": raw, "valid_kinds": list(valid)})


def resolve_rag_server_url(call_kwargs: Mapping[str, Any]) -> str:
    """Resolve the RAG endpoint and publish it through the pinned side-channel.

    Precedence verbatim from the legacy branches: explicit kwarg, then the
    environment, then the localhost default. The `os.environ` write stays until
    composition passes the URL explicitly (spec-0004 debt, also noted on the
    knowledgebase brain) — characterization tests pin the write itself.

    Args:
        call_kwargs: The call's keyword arguments.

    Returns:
        The resolved URL (also published to the environment as a side effect).
    """
    url: str = call_kwargs.get("rag_server_url", os.getenv(_RAG_SERVER_URL_ENV, _DEFAULT_RAG_SERVER_URL))
    os.environ[_RAG_SERVER_URL_ENV] = url
    return url


def inject_shared_call_context(
    base: dict[str, Any],
    *,
    call_kwargs: Mapping[str, Any],
    context_data: Any,
    llm_flags: Mapping[str, Any],
    buffer_size: Any,
    language: Any,
) -> dict[str, Any]:
    """Merge call kwargs into a brain config — the ONCE-shared merge (spec 0015).

    The graph and knowledgebase legacy branches duplicated these ~15 lines with
    byte-identical semantics; both now call here so the two brains can never
    drift on credential/flag plumbing again.

    Args:
        base: The task's `llm_config` dict (copied by the caller; mutated in place).
        call_kwargs: The call's keyword arguments (credentials, routing tuning).
        context_data: Call context merged for prompt variable replacement.
        llm_flags: The composed LLM config (only truthy `use_responses_api` /
            `compact_threshold` promote — legacy quirk preserved).
        buffer_size: Synthesizer buffer size stamped for audio pacing.
        language: Active call language stamped for the brain.

    Returns:
        `base` mutated, for chaining.
    """
    for key in _SHARED_KWARG_KEYS:
        if key in call_kwargs:
            base[key] = call_kwargs[key]
    if context_data:
        base["context_data"] = context_data
    for key in _LLM_FLAG_KEYS:
        if llm_flags.get(key):
            base[key] = True if key == "use_responses_api" else llm_flags[key]
    base["buffer_size"] = buffer_size
    base["language"] = language
    return base


def _task_llm_agent(session: Any) -> dict[str, Any]:  # why: the legacy session object is untyped at the seam
    """Read the task's `llm_agent` mapping off a legacy session object.

    Strictness mirrors the legacy branches exactly: `task_config` /
    `tools_config` / `llm_agent` subscript directly (a malformed task raises
    `KeyError`, then as now), while `llm_config` defaults to `{}`.

    Args:
        session: The call session (duck-typed; carries `task_config`).

    Returns:
        The `llm_agent` mapping (possibly empty — kind resolution decides next).
    """
    tools_config = session.task_config["tools_config"]
    llm_agent: dict[str, Any] = tools_config["llm_agent"]
    return llm_agent


def _default_constructors() -> dict[str, Callable[..., Any]]:
    """Bind the shipped brains without importing them at module load.

    Late imports keep ``voiceai.modules.agents`` import-light (the A2 canary
    discipline): the graph package alone is 2000+ lines with optional third-party
    probes at import time. These are the SAME classes the legacy shims re-export,
    so default-built brains are behavior-identical to legacy-built ones.

    Returns:
        Fresh default registry (callers may mutate their copy freely).
    """
    from voiceai.modules.agents.brains.graph import GraphAgent
    from voiceai.modules.agents.brains.knowledgebase import KnowledgeBaseAgent
    from voiceai.modules.agents.brains.simple import StreamingContextualAgent

    return {
        AGENT_TYPE_SIMPLE_LLM: StreamingContextualAgent,
        AGENT_TYPE_GRAPH: GraphAgent,
        AGENT_TYPE_KNOWLEDGEBASE: KnowledgeBaseAgent,
    }


class BrainFactory:
    """Build conversation brains from engine kinds with injected constructors.

    Args:
        constructors: Kind → callable map. Values take the assembled
            `injected_cfg` dict, EXCEPT the simple brain which takes the composed
            `llm` positionally (legacy calling convention, preserved). `None`
            installs the late-bound new-home defaults. Copied on receipt, so
            per-test `register()` calls never leak across instances.
    """

    def __init__(self, constructors: Mapping[str, Callable[..., Any]] | None = None) -> None:
        self._constructors: dict[str, Callable[..., Any]] = (
            dict(constructors) if constructors is not None else _default_constructors()
        )

    def register(self, kind: str, constructor: Callable[..., Any]) -> None:
        """Add or replace the constructor for ``kind`` (tests/tenants).

        Args:
            kind: Engine kind (or tenant extension — `build` accepts any
                registered kind, not just `ENGINE_KINDS`).
            constructor: Callable taking the assembled config (or `llm` for the
                simple shape — match the kind you replace).
        """
        self._constructors[kind] = constructor

    def build(self, agent_type: str | None, llm: Any, session: Any) -> BrainPort:  # why: legacy session/llm are untyped
        """Build the conversation brain for one task (spec 0015).

        Args:
            agent_type: Raw task-level type (`llm_agent.agent_type`); missing or
                unknown values raise `AgentsError` (never the legacy string-raise).
            llm: The composed conversation LLM (simple brain only; graph/KB build
                their own from the injected config, exactly as legacy does).
            session: The legacy call session; task config, kwargs, context, flags,
                and synthesizer sizing are read off it defensively.

        Returns:
            A `BrainPort`-conforming brain.

        Raises:
            AgentsError: On unknown/missing kind (never the legacy string-raise).
                Malformed tasks raise exactly as the legacy branches did
                (`KeyError` on strict subscripts) — parity by construction.
        """
        kind = resolve_kind(agent_type, valid=tuple(self._constructors))
        # Session reads mirror the legacy branches exactly: strict subscripts where
        # legacy subscripted (malformed tasks raise identically), `.get` defaults
        # where legacy defaulted, direct attributes elsewhere.
        call_kwargs: Mapping[str, Any] = session.kwargs
        tools_config = session.task_config["tools_config"]
        task_llm_agent = _task_llm_agent(session)
        llm_config = task_llm_agent.get("llm_config", {}) or {}
        synthesizer = tools_config["synthesizer"]
        session_llm_config = session.llm_config or {}

        if kind == AGENT_TYPE_SIMPLE_LLM:
            # why: the simple ctor takes the composed llm positionally (legacy convention)
            return cast("BrainPort", self._constructors[kind](llm))

        injected = inject_shared_call_context(
            dict(llm_config),
            call_kwargs=call_kwargs,
            context_data=session.context_data,
            llm_flags=session_llm_config,
            buffer_size=synthesizer.get("buffer_size"),
            language=session.language,
        )
        if kind == AGENT_TYPE_GRAPH:
            logger.info(_LOG_SETTING_UP_GRAPH)
            logger.info(_LOG_GRAPH_CONFIG, llm_config)
            rag_url = resolve_rag_server_url(call_kwargs)
            logger.info(_LOG_RAG_URL, rag_url)
            for key in _GRAPH_KWARG_KEYS:
                if key in call_kwargs:
                    injected[key] = call_kwargs[key]
            injected["turn_based_conversation"] = session.turn_based_conversation
            injected["execution_id"] = session.run_id
            # why: heterogeneous legacy ctors; conformance is structural at runtime
            brain = cast("BrainPort", self._constructors[kind](injected))
            logger.info(_LOG_GRAPH_CREATED)
            return brain

        logger.info(_LOG_SETTING_UP_KNOWLEDGE)
        logger.info(_LOG_KNOWLEDGE_CONFIG, llm_config)
        rag_url = resolve_rag_server_url(call_kwargs)
        logger.info(_LOG_RAG_URL, rag_url)
        # why: heterogeneous legacy ctors; conformance is structural at runtime
        brain = cast("BrainPort", self._constructors[kind](injected))
        logger.info(_LOG_KNOWLEDGE_CREATED)
        return brain
