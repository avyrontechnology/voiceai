"""Voice-side seam for tenant-scoped tool execution (spec 0056, Phase-1).

Voice imports only this Protocol surface — never tools internals (AGENTS.md
§3.1, bridge 4 pattern): the file carries no `voiceai.modules.tools`
imports, so the layer contract cannot trip on it. Conformance is structural:
`tools.ToolExecutor.execute_tool` satisfies `ToolExecutorPort` without either
side importing the other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

__all__ = ["AgentSnapshotPort", "ToolExecutionResultPort", "ToolExecutorPort"]


class AgentSnapshotPort(Protocol):
    """Owning agent as the voice side sees it (allowlist principal + refs)."""

    @property
    def agent_id(self) -> str:
        """The owning agent id."""
        ...

    @property
    def tool_refs(self) -> Sequence[str]:
        """The attached canonical tool ids the agent may execute."""
        ...


class ToolExecutionResultPort(Protocol):
    """Outcome as the voice side consumes it (success flag plus payload)."""

    @property
    def tool_id(self) -> str:
        """The canonical executed id."""
        ...

    @property
    def agent_id(self) -> str:
        """The owning agent id."""
        ...

    @property
    def success(self) -> bool:
        """Whether the call answered successfully."""
        ...

    @property
    def result_summary(self) -> str:
        """Short safe outcome line (never secrets)."""
        ...

    @property
    def data(self) -> Mapping[str, Any]:  # why: upstream tool payloads are free-form JSON
        """Upstream payload for the caller (in-memory only)."""
        ...

    @property
    def call_fingerprint(self) -> str:
        """Stable idempotency fingerprint of id + arguments."""
        ...

    @property
    def execution_ms(self) -> int:
        """Guarded wall time."""
        ...

    @property
    def error_id(self) -> str | None:
        """Log-stitching id for failures."""
        ...


class ToolExecutorPort(Protocol):
    """What voice (and chat) call instead of tools internals (spec 0056)."""

    async def execute_tool(
        self,
        *,
        tool_id: str,
        arguments: Mapping[str, Any],  # why: call arguments are free-form JSON
        agent_snapshot: AgentSnapshotPort,
        tenant: str,
    ) -> ToolExecutionResultPort:
        """Execute one tool call for an agent, guarded and audit-logged.

        Args:
            tool_id: Possibly MCP-namespaced id (stripped before the allowlist).
            arguments: Call arguments.
            agent_snapshot: Owning agent (allowlist principal + attached refs).
            tenant: Acting tenant (stamped on the audit row).

        Returns:
            The outcome (upstream failures arrive as `success=False`).
        """
        ...
