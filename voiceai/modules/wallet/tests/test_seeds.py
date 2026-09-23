"""Seed catalog contract: every seed template must satisfy the create-AgentModel (T5b).

The import flow feeds a template's `agent_payload` straight into `POST /agent`
as `agent_config` — so a seed row that fails `AgentModel` validation is a 422
waiting to happen (observed: `provider: "simulated"` in task IO blocks, rejected
because the runtime only knows real IO providers). These pins keep seed data and
the validation contract in lockstep.
"""

from __future__ import annotations

from voiceai.modules.agents.models import AgentModel
from voiceai.modules.wallet.templates import TEMPLATES


def test_every_seed_payload_validates_as_an_agent() -> None:
    """Each seed `agent_payload` passes `AgentModel` (the import→create contract)."""
    assert TEMPLATES, "seed catalog must not be empty"
    for template in TEMPLATES:
        AgentModel.model_validate(template.agent_payload)


def test_every_seed_payload_has_a_conversation_task() -> None:
    """Seed payloads carry at least one task with IO blocks the engine can read."""
    for template in TEMPLATES:
        tasks = template.agent_payload["tasks"]
        assert isinstance(tasks, list) and tasks, template.template_id
        tools = tasks[0]["tools_config"]
        assert tools["input"]["provider"]
        assert tools["output"]["provider"]
