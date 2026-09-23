"""Structural contract tests for the agents module's ports (spec 0002, step A1).

Import-only and offline: fakes conform to the protocols, non-conformers are rejected, and
one typed assignment per port makes the conformance an explicit mypy check (spec 0001 test
plan pattern).
"""

from __future__ import annotations

from typing import Any

import pytest

from voiceai.modules.agents.constants import AGENT_DATA_KEY, AGENT_ID_KEY
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort

AGENT_ID = "0f7c0d5e-2f4b-4d76-9a4a-agent-a1"
AGENT_CONFIG = {"agent_name": "a1", "tasks": []}
PROMPTS = {"task_1": {"system_prompt": "hello"}}

DEFINITION_PORT_METHODS = frozenset({"get_agent", "save_agent", "delete_agent", "list_agents"})
SESSION_STORE_PORT_METHODS = frozenset({"get_prompts", "save_prompts", "delete_prompts"})


class FakeDefinitionStore:
    """In-memory conformer to `AgentDefinitionPort`, fully annotated for the mypy pin."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Answer the stored config or `None`."""
        return self.records.get(agent_id)

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Overwrite the stored config."""
        self.records[agent_id] = config

    async def delete_agent(self, agent_id: str) -> bool:
        """Drop the record; report whether one existed."""
        return self.records.pop(agent_id, None) is not None

    async def list_agents(self) -> list[dict[str, Any]]:
        """Answer the quickstart record shape for every stored agent."""
        return [{AGENT_ID_KEY: agent_id, AGENT_DATA_KEY: data} for agent_id, data in self.records.items()]


class FakeSessionStore:
    """In-memory conformer to `AgentSessionStorePort`, fully annotated for the mypy pin."""

    def __init__(self) -> None:
        self.prompts: dict[str, dict[str, Any] | None] = {}

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Answer the stored prompts or `None`."""
        return self.prompts.get(agent_id)

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Persist the prompt payload (possibly `None`)."""
        self.prompts[agent_id] = prompts

    async def delete_prompts(self, agent_id: str) -> bool:
        """Drop the payload; report whether one existed."""
        return self.prompts.pop(agent_id, None) is not None


class NotAPort:
    """Deliberately implements none of the port methods."""


def test_definition_fake_satisfies_the_port():
    """Structural conformance, both at runtime and (via the annotation) under mypy."""
    port: AgentDefinitionPort = FakeDefinitionStore()

    assert isinstance(port, AgentDefinitionPort)


def test_session_store_fake_satisfies_the_port():
    """Structural conformance, both at runtime and (via the annotation) under mypy."""
    port: AgentSessionStorePort = FakeSessionStore()

    assert isinstance(port, AgentSessionStorePort)


def test_unrelated_object_conforms_to_neither_port():
    """`runtime_checkable` must actually discriminate, not accept everything."""
    stranger = NotAPort()

    assert not isinstance(stranger, AgentDefinitionPort)
    assert not isinstance(stranger, AgentSessionStorePort)


def test_the_ports_split_the_seven_contract_methods():
    """Renaming or moving a port method must be loud: the ports name exactly these."""
    assert DEFINITION_PORT_METHODS <= frozenset(dir(AgentDefinitionPort))
    assert SESSION_STORE_PORT_METHODS <= frozenset(dir(AgentSessionStorePort))
    assert not DEFINITION_PORT_METHODS & frozenset(dir(NotAPort))


def test_ports_are_not_instantiable():
    """A protocol is a contract, not a class anyone constructs."""
    with pytest.raises(TypeError):
        AgentDefinitionPort()  # type: ignore[misc]  # the point: instantiation must raise
    with pytest.raises(TypeError):
        AgentSessionStorePort()  # type: ignore[misc]  # the point: instantiation must raise


async def test_definition_port_round_trip_through_the_protocol_type():
    """Calls typed against the port drive a conforming fake end to end."""
    port: AgentDefinitionPort = FakeDefinitionStore()

    assert await port.get_agent(AGENT_ID) is None
    await port.save_agent(AGENT_ID, AGENT_CONFIG)
    assert await port.get_agent(AGENT_ID) == AGENT_CONFIG
    assert await port.list_agents() == [{AGENT_ID_KEY: AGENT_ID, AGENT_DATA_KEY: AGENT_CONFIG}]
    assert await port.delete_agent(AGENT_ID) is True
    assert await port.delete_agent(AGENT_ID) is False


async def test_session_store_port_round_trip_through_the_protocol_type():
    """Missing prompts answer `None` (the degrade-to-empty legacy contract), then round-trip."""
    port: AgentSessionStorePort = FakeSessionStore()

    assert await port.get_prompts(AGENT_ID) is None
    await port.save_prompts(AGENT_ID, PROMPTS)
    assert await port.get_prompts(AGENT_ID) == PROMPTS
