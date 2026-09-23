"""Wire DTOs for the agents module: request shapes, strictly typed (T3).

Talko parity (`TalkoContract` in each component's `dto.py`): the create/update
request body lives here instead of on the controller. Responses stay raw engine
dicts by contract (the engine seam is free-form JSON), so no `response_model`
constrains them — a model would silently strip unknown fields. Controllers,
services and repositories never shape responses; tests pin them byte-identical.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.agents.models import AgentModel

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
