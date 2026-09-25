"""Wire DTOs for the agents module: request shapes, strictly typed (T3).

Talko parity (`TalkoContract` in each component's `dto.py`): the create/update
request body lives here instead of on the controller. Responses stay raw engine
dicts by contract (the engine seam is free-form JSON), so no `response_model`
constrains them — a model would silently strip unknown fields. Controllers,
services and repositories never shape responses; tests pin them byte-identical.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.models.agent import Task
from voiceai.modules.agents.models.channel import Channel, Pipeline

__all__ = ["AgentsContract"]


class AgentsContract:
    """Namespace for the agents wire shapes (talko `TalkoContract` shape, strict)."""

    class CreateAgentRequest(BaseModel):
        """Request body for agent create and update — the exact quickstart payload contract."""

        agent_config: AgentModel = Field(
            ..., description="The main agent configuration including tools, tasks, and settings."
        )
        # Values are usually strings (system_prompt, welcome_message) but may be nested blocks
        # such as task_1.multilingual_prompts, which the engine reads at runtime.
        agent_prompts: dict[str, dict[str, Any]] | None = Field(  # why: prompt blocks are free-form JSON
            default=None, description="Optional prompts mapped by intent/context."
        )

    class TaskPatch(BaseModel):
        """One addressed task edit: merge into `tasks[task_index]` (spec 0028)."""

        model_config = {"extra": "forbid"}

        task_index: int = Field(..., ge=0, description="Index into the agent's tasks array.")
        task_type: str | None = Field(default=None)
        pipeline: Pipeline | None = Field(default=None, description="Present-null is a no-op; clear via `clear`.")
        tools_config: dict[str, Any] | None = Field(  # why: component values re-validate as full strict models
            default=None, description="Merged key-by-key; each present component replaces wholesale."
        )
        toolchain: dict[str, Any] | None = Field(default=None, description="Replaces wholesale when present.")
        task_config: dict[str, Any] | None = Field(default=None, description="Merged key-by-key.")
        clear: list[Literal["pipeline"]] = Field(
            default_factory=list, description="Explicit clears (null is a no-op, never a clear)."
        )

    class PatchAgentRequest(BaseModel):
        """Request body for `PATCH /agent/{id}` — strict partials (spec 0028)."""

        model_config = {"extra": "forbid"}

        agent_name: str | None = Field(default=None)
        agent_type: str | None = Field(default=None)
        agent_welcome_message: str | None = Field(default=None)
        channels: list[Channel] | None = Field(default=None, description="Replaces wholesale when present.")
        tasks: list[Task] | None = Field(default=None, description="Full-array replace; exclusive with `tasks_patch`.")
        tasks_patch: list[AgentsContract.TaskPatch] | None = Field(
            default=None, description="Per-index edits; exclusive with `tasks`."
        )
        agent_prompts: dict[str, dict[str, Any]] | None = Field(  # why: prompt blocks are free-form JSON
            default=None, description="Replaces wholesale when present."
        )
        clear: list[Literal["agent_prompts"]] = Field(
            default_factory=list, description="Explicit clears (null is a no-op, never a clear)."
        )
