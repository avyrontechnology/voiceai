"""Wire-contract pins: the request body validates, garbage does not (T3).

Responses stay raw engine dicts by contract (no `response_model` constrains them —
documented in the controller), so these tests pin the request side: the quickstart
payload validates through `AgentsContract`, and malformed bodies fail DTO
validation rather than reaching the service.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.modules.agents.schemas import AgentsContract

VALID_CONFIG = {
    "agent_name": "Support",
    "tasks": [{"tools_config": {}, "toolchain": {"execution": "sequential", "pipelines": []}}],
}


def test_create_request_accepts_config_with_optional_prompts() -> None:
    """The quickstart payload validates with and without the prompt blocks."""
    full = AgentsContract.CreateAgentRequest.model_validate(
        {"agent_config": VALID_CONFIG, "agent_prompts": {"task_1": {"system_prompt": "Hi"}}}
    )
    bare = AgentsContract.CreateAgentRequest.model_validate({"agent_config": VALID_CONFIG})

    assert full.agent_config.agent_name == "Support"
    assert bare.agent_prompts is None


def test_create_request_rejects_missing_config() -> None:
    """A missing `agent_config` fails DTO validation (the 422 path)."""
    with pytest.raises(ValidationError):
        AgentsContract.CreateAgentRequest.model_validate({"agent_prompts": {}})
