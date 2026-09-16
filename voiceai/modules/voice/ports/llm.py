"""Generation ports: the brain/LLM seam of the call runtime (spec 0004 design).

`LlmPort` is the streaming-generation seam alone; `AgentBrainPort` layers the completion
judgment on top of it (what task_manager drives as ``tools["llm_agent"]``); and
`GraphBrainPort` adds the graph-agent extension surface (events, nodes, context).
Message dicts pass through opaque — the LLM wire shape is the seam, never a model
(mirroring the agents module's census-verified dict seam).

The history parameter is positional-only and the options keyword-only on purpose: the
concrete brains disagree on the parameter NAMES (``history`` vs ``message``), and the
graph brain absorbs the options via ``**kwargs`` — this signature is the one all of
them satisfy structurally. Conformance pins against the real brains land with the
session steps; at B0 only fakes conform (the six-legacy-class list of the B0 gate does
not include a brain).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

__all__ = ["AgentBrainPort", "GraphBrainPort", "LlmPort"]


@runtime_checkable
class LlmPort(Protocol):
    """One conversational turn streamed: chat history in, response stream out."""

    def generate(
        self,
        history: list[dict[str, Any]],  # why: free-form legacy chat history is the seam
        /,
        *,
        synthesize: bool = ...,
        meta_info: dict[str, Any] | None = ...,  # why: the engine's free-form call metadata
    ) -> AsyncIterator[Any]:  # why: yields text chunks, routing signals, or provider payloads
        """Stream one response for ``history`` (tokens, plus brain-specific signals)."""


@runtime_checkable
class AgentBrainPort(LlmPort, Protocol):
    """The conversational brain the runtime drives: generation plus hangup judgment."""

    async def check_for_completion(
        self,
        messages: list[dict[str, Any]],  # why: free-form legacy chat messages
        check_for_completion_prompt: str,
        meta_info: dict[str, Any] | None = ...,  # why: the engine's free-form call metadata
    ) -> tuple[dict[str, Any], dict[str, Any]]:  # why: (hangup_dict, metadata) legacy judge shape
        """Ask the judgment LLM whether the conversation should end."""


@runtime_checkable
class GraphBrainPort(AgentBrainPort, Protocol):
    """The graph-agent extension surface task_manager consumes beyond the base brain."""

    @property
    def context_data(self) -> dict[str, Any]:  # why: free-form per-call context merged by events
        """Per-call context (recipient data, detected language, last event, ...)."""

    @property
    def current_node_entry_index(self) -> int:
        """History index where the current node was entered."""

    # TODO(spec-0004): preserved encapsulation leak — task_manager reads this private
    # flag directly to tell event-triggered generations apart; the turn steps (B11)
    # decide whether it becomes a method or stays a read.
    @property
    def _event_triggered_generation(self) -> bool:
        """Whether the in-flight generation was triggered by an external event."""

    def process_event(self, event: dict[str, Any]) -> Any:  # why: free-form event in, node-decision out
        """Merge an external event into context and answer the routing consequence."""

    def get_node_by_id(self, node_id: str) -> dict[str, Any] | None:  # why: raw graph node dicts are the seam
        """The raw graph node for ``node_id``, or ``None`` when unknown."""

    def update_current_node(self) -> None:
        """Commit the pending node transition after a delivered response."""

    def mark_first_response_delivered(self) -> None:
        """Record that the first response reached the caller (routing warm-up gate)."""
