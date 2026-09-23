"""Mongo-backed adapters: port conformance over in-memory collections (T3).

Drives the REAL `MongoAgentDefinitions`/`MongoAgentPrompts` over `InMemoryDatabase`
repositories (rule 9 — production swaps in motor behind the same `BaseRepository`
protocol): natural-key pinning, genuine-record filtering without scans, soft-delete
semantics, and prompts-first deletion pairing at the service level.
"""

from __future__ import annotations

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.agents.models.definition import AgentDefinition
from voiceai.modules.agents.models.prompts import AgentPrompts
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort
from voiceai.modules.agents.repository import MongoAgentDefinitions, MongoAgentPrompts

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
CONFIG = {"agent_name": "Support", "agent_type": "other", "tasks": []}
PROMPTS = {"task_1": {"system_prompt": "You are support."}}


def _definitions(database: InMemoryDatabase | None = None) -> MongoAgentDefinitions:
    """Build the definitions adapter over a fresh (or given) in-memory database."""
    database = database if database is not None else InMemoryDatabase()
    return MongoAgentDefinitions(InMemoryRepository(database, Collections.AGENTS, AgentDefinition))


def _prompts(database: InMemoryDatabase | None = None) -> MongoAgentPrompts:
    """Build the prompts adapter over a fresh (or given) in-memory database."""
    database = database if database is not None else InMemoryDatabase()
    return MongoAgentPrompts(InMemoryRepository(database, Collections.AGENT_PROMPTS, AgentPrompts))


def test_adapters_satisfy_the_ports() -> None:
    """Structural conformance, both at runtime and (via the annotation) under mypy."""
    definitions: AgentDefinitionPort = _definitions()
    prompts: AgentSessionStorePort = _prompts()

    assert isinstance(definitions, AgentDefinitionPort)
    assert isinstance(prompts, AgentSessionStorePort)


async def test_definitions_round_trip_crud() -> None:
    """Save → get → overwrite → soft-delete → get-misses, keyed by agent id."""
    store = _definitions()

    assert await store.get_agent(AGENT_ID) is None
    await store.save_agent(AGENT_ID, dict(CONFIG))
    assert await store.get_agent(AGENT_ID) == CONFIG

    updated = dict(CONFIG, agent_name="Support v2")
    await store.save_agent(AGENT_ID, updated)
    assert await store.get_agent(AGENT_ID) == updated

    assert await store.delete_agent(AGENT_ID) is True
    assert await store.delete_agent(AGENT_ID) is False
    assert await store.get_agent(AGENT_ID) is None


async def test_directory_lists_genuine_records_with_stable_ids() -> None:
    """The `/all` acceptance rules hold without scans: non-agent configs are skipped
    while genuine records keep their own ids (never a positional zip)."""
    store = _definitions()
    await store.save_agent(AGENT_ID, dict(CONFIG))
    await store.save_agent("bare-but-not-agent", {"no": "tasks"})

    records = await store.list_agents()

    assert records == [{"agent_id": AGENT_ID, "data": CONFIG}]


async def test_directory_is_empty_without_records() -> None:
    """No rows, no payload — never an error."""
    assert await _definitions().list_agents() == []


async def test_prompts_round_trip_save_get_delete() -> None:
    """Payloads ride opaque; missing reads `None`; deletes report existence."""
    store = _prompts()

    assert await store.get_prompts(AGENT_ID) is None
    await store.save_prompts(AGENT_ID, dict(PROMPTS))
    assert await store.get_prompts(AGENT_ID) == PROMPTS

    await store.save_prompts(AGENT_ID, None)
    assert await store.get_prompts(AGENT_ID) is None

    await store.save_prompts(AGENT_ID, dict(PROMPTS))
    assert await store.delete_prompts(AGENT_ID) is True
    assert await store.delete_prompts(AGENT_ID) is False
    assert await store.get_prompts(AGENT_ID) is None
