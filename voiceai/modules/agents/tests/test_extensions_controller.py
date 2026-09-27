"""Slice D wire contract: `extensions` PATCH merge/clear/validation over HTTP (spec 0043).

Controller tests through the real app factory (`httpx` ASGI transport, offline only):
`tasks_patch[].task_config.extensions` merges key-by-key, `clear_extensions` drops
named keys after the merge, and an invalid key syntax answers 400 with the opaque
envelope (error id present, the offending value never echoed). `controller.py` is
unchanged — the `Create`/`Patch` aliases flow the new `TaskPatch` fields through the
untouched handlers.
"""

from __future__ import annotations

import pytest
from dependency_injector import providers

from voiceai.common.constants import (
    API_PREFIX,
    HTTP_BAD_REQUEST,
    HTTP_CREATED,
    HTTP_OK,
    HTTP_UNPROCESSABLE_ENTITY,
)
from voiceai.common.errors import ErrorCode
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.agents.constants import AGENT_BY_ID_PATH, AGENT_PATH
from voiceai.modules.agents.models.definition import AgentDefinition
from voiceai.modules.agents.models.prompts import AgentPrompts
from voiceai.modules.agents.repository import MongoAgentDefinitions, MongoAgentPrompts

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"

AGENT_URL = f"{API_PREFIX}{AGENT_PATH}"
CREATE_PAYLOAD = {
    "agent_config": {
        "agent_name": "Support",
        "tasks": [{"tools_config": {}, "toolchain": {"execution": "sequential", "pipelines": []}}],
    },
}
INVALID_KEY = "bad.key"
SECRET_VALUE = "SECRET-EXTENSION-VALUE-MUST-NEVER-ECHO"


def agent_url(agent_id):
    """The prefixed URL of one agent's config route."""
    return f"{API_PREFIX}{AGENT_BY_ID_PATH.format(agent_id=agent_id)}"


async def _create_agent(agents_client):
    """POST the minimal agent and return its fresh id (raises on any setup failure)."""
    created = await agents_client.post(AGENT_URL, json=CREATE_PAYLOAD)
    assert created.status_code == HTTP_CREATED
    return created.json()["data"]["agent_id"]


async def _task_config(agents_client, agent_id):
    """Read back the first task's full `task_config` mapping through GET."""
    read = await agents_client.get(agent_url(agent_id))
    assert read.status_code == HTTP_OK
    return read.json()["data"]["tasks"][0]["task_config"]


@pytest.fixture
def agent_stores():
    """Fresh Mongo-backed adapters over an in-memory database, pre-seeded with one agent."""
    database = InMemoryDatabase()
    definitions = MongoAgentDefinitions(InMemoryRepository(database, Collections.AGENTS, AgentDefinition))
    prompts = MongoAgentPrompts(InMemoryRepository(database, Collections.AGENT_PROMPTS, AgentPrompts))
    return definitions, prompts, database


@pytest.fixture
async def agents_client(arch_app, client_factory, agent_stores):
    """The real app with the Mongo adapters swapped in under the agent providers."""
    definitions, prompt_store, _database = agent_stores
    await definitions.save_agent(AGENT_ID, {"agent_name": "Support", "agent_type": "other", "tasks": []})
    container = arch_app.state.container
    container.agent_definitions.override(providers.Object(definitions))
    container.agent_session_store.override(providers.Object(prompt_store))
    async with client_factory(arch_app, raise_app_exceptions=False) as client:
        yield client


async def test_patch_task_config_extensions_merge_key_by_key(agents_client):
    """PATCH `task_config.extensions` adds keys without clobbering existing ones."""
    agent_id = await _create_agent(agents_client)

    first = await agents_client.patch(
        agent_url(agent_id),
        json={"tasks_patch": [{"task_index": 0, "task_config": {"extensions": {"my_flag": "on"}}}]},
    )
    assert first.status_code == HTTP_OK
    assert (await _task_config(agents_client, agent_id))["extensions"] == {"my_flag": "on"}

    second = await agents_client.patch(
        agent_url(agent_id),
        json={"tasks_patch": [{"task_index": 0, "task_config": {"extensions": {"other_flag": 3}}}]},
    )
    assert second.status_code == HTTP_OK
    assert (await _task_config(agents_client, agent_id))["extensions"] == {"my_flag": "on", "other_flag": 3}


async def test_patch_clear_extensions_drops_only_named_keys(agents_client):
    """`clear_extensions` removes the named keys after the merge, leaving the rest."""
    agent_id = await _create_agent(agents_client)
    seeded = await agents_client.patch(
        agent_url(agent_id),
        json={"tasks_patch": [{"task_index": 0, "task_config": {"extensions": {"gone": 1, "stays": 2}}}]},
    )
    assert seeded.status_code == HTTP_OK

    cleared = await agents_client.patch(
        agent_url(agent_id), json={"tasks_patch": [{"task_index": 0, "clear_extensions": ["gone"]}]},
    )
    assert cleared.status_code == HTTP_OK
    assert (await _task_config(agents_client, agent_id))["extensions"] == {"stays": 2}


async def test_patch_invalid_extension_key_syntax_answers_400_without_value_echo(agents_client):
    """A dotted key (outside the allowlist syntax) is a 400 opaque envelope: error id logged,
    the offending value never echoed, and nothing persisted."""
    agent_id = await _create_agent(agents_client)

    response = await agents_client.patch(
        agent_url(agent_id),
        json={"tasks_patch": [{"task_index": 0, "task_config": {"extensions": {INVALID_KEY: SECRET_VALUE}}}]},
    )
    body = response.json()

    assert response.status_code == HTTP_BAD_REQUEST
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.INVALID_REQUEST.value
    assert body["error"]["error_id"]
    assert SECRET_VALUE not in response.text
    assert "extensions" not in (await _task_config(agents_client, agent_id))


async def test_patch_rejects_unknown_top_level_key(agents_client):
    """`PatchAgentRequest` stays `extra="forbid"`: unknown wire keys are a 422 envelope."""
    agent_id = await _create_agent(agents_client)

    response = await agents_client.patch(agent_url(agent_id), json={"no_such_field": True})
    body = response.json()

    assert response.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.INVALID_REQUEST.value
