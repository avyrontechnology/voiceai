"""The agents endpoints end to end: real app factory, real MODULE, ASGI transport (T3).

The only fakes go in through the composition seams the production wiring uses:
`MongoAgentDefinitions`/`MongoAgentPrompts` over a fresh `InMemoryDatabase`,
re-registered under the container's agent providers (production swaps in motor
behind the same `BaseRepository` protocol). Every response is the spec 0001
envelope with the quickstart payload byte-identical inside `data`, and the legacy
quirks are pinned by name: the 404-swallowed-to-500 on GET/PUT/DELETE
`/agent/{agent_id}`, the true 404 on the prompts route, falsy prompts as null.
T3 retires the prompt-file orphan on DELETE (payload dies with the definition).
"""

from __future__ import annotations

import pytest
from dependency_injector import providers

from voiceai.common.constants import (
    API_PREFIX,
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_NOT_FOUND,
    HTTP_OK,
    HTTP_SERVICE_UNAVAILABLE,
    HTTP_UNPROCESSABLE_ENTITY,
    REQUEST_ID_HEADER,
)
from voiceai.common.errors import ErrorCode
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.agents.constants import (
    AGENT_BY_ID_PATH,
    AGENT_PATH,
    AGENT_PROMPTS_PATH,
    ALL_AGENTS_PATH,
)
from voiceai.modules.agents.models.definition import AgentDefinition
from voiceai.modules.agents.models.prompts import AgentPrompts
from voiceai.modules.agents.repository import MongoAgentDefinitions, MongoAgentPrompts

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
MISSING_ID = "00000000-0000-0000-0000-000000000000"
HTTP_CREATED = 201

AGENT_URL = f"{API_PREFIX}{AGENT_PATH}"
ALL_URL = f"{API_PREFIX}{ALL_AGENTS_PATH}"

STORED_CONFIG = {"agent_name": "Support", "agent_type": "other", "tasks": []}
NON_AGENT_CONFIG = {"no": "tasks"}
PROMPTS = {"task_1": {"system_prompt": "You are support.", "multilingual_prompts": {}}}
CREATE_PAYLOAD = {
    "agent_config": {
        "agent_name": "Support",
        "tasks": [{"tools_config": {}, "toolchain": {"execution": "sequential", "pipelines": []}}],
    },
    "agent_prompts": PROMPTS,
}


def agent_url(agent_id):
    """The prefixed URL of one agent's config route."""
    return f"{API_PREFIX}{AGENT_BY_ID_PATH.format(agent_id=agent_id)}"


def prompts_url(agent_id):
    """The prefixed URL of one agent's prompts route."""
    return f"{API_PREFIX}{AGENT_PROMPTS_PATH.format(agent_id=agent_id)}"


@pytest.fixture
def agent_stores():
    """Fresh Mongo-backed adapters over an in-memory database, pre-seeded with one agent."""
    database = InMemoryDatabase()
    definitions = MongoAgentDefinitions(InMemoryRepository(database, Collections.AGENTS, AgentDefinition))
    prompts = MongoAgentPrompts(InMemoryRepository(database, Collections.AGENT_PROMPTS, AgentPrompts))
    return definitions, prompts, database


@pytest.fixture
async def agents_client(arch_app, client_factory, agent_stores):
    """The real app with the Mongo adapters swapped in under the agent providers.

    `raise_app_exceptions=False` because half the pins below assert on 500 envelopes.
    """
    definitions, prompt_store, _database = agent_stores
    await definitions.save_agent(AGENT_ID, dict(STORED_CONFIG))
    container = arch_app.state.container
    container.agent_definitions.override(providers.Object(definitions))
    container.agent_session_store.override(providers.Object(prompt_store))
    async with client_factory(arch_app, raise_app_exceptions=False) as client:
        yield client


@pytest.fixture
async def no_store_client(arch_app, client_factory):
    """The real app with no definition store (the 503 branch, not a crash)."""
    arch_app.state.container.agent_definitions.override(providers.Object(None))
    async with client_factory(arch_app, raise_app_exceptions=False) as client:
        yield client


# --- create + read back --------------------------------------------------------------------


async def test_create_then_read_round_trips_through_the_envelope(agents_client, agent_stores):
    """POST answers 201 with the quickstart `{"agent_id", "state"}` payload as data, and the
    stored record carries the seeded status readable back through GET `/agent/{id}`."""
    definitions, _prompts, _database = agent_stores
    created = await agents_client.post(AGENT_URL, json=CREATE_PAYLOAD)
    body = created.json()

    assert created.status_code == HTTP_CREATED
    assert body["ok"] is True
    assert body["data"]["state"] == "created"
    agent_id = body["data"]["agent_id"]
    assert set(body["data"]) == {"agent_id", "state"}

    read = await agents_client.get(agent_url(agent_id))
    read_body = read.json()

    assert read.status_code == HTTP_OK
    assert read_body["ok"] is True
    assert read_body["data"]["assistant_status"] == "seeding"  # legacy-parity
    assert read_body["data"]["agent_name"] == "Support"
    assert read_body["data"] == await definitions.get_agent(agent_id)


async def test_create_writes_the_prompt_payload_to_the_store(agents_client, agent_stores):
    """The prompt payload lands in the prompts collection, not on disk."""
    _definitions, prompt_store, _database = agent_stores
    created = await agents_client.post(AGENT_URL, json=CREATE_PAYLOAD)
    agent_id = created.json()["data"]["agent_id"]

    assert await prompt_store.get_prompts(agent_id) == PROMPTS


async def test_create_rejects_a_payload_without_agent_config(agents_client):
    """Boundary validation: a missing `agent_config` is a 422 error envelope."""
    response = await agents_client.post(AGENT_URL, json={"agent_prompts": PROMPTS})
    body = response.json()

    assert response.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.INVALID_REQUEST.value


# --- the swallowed 404 (legacy-parity) -----------------------------------------------------


async def test_get_missing_agent_answers_the_swallowed_500_not_a_404(agents_client):
    """# legacy-parity(spec-0002): quickstart's bare `except Exception` turns its own 404
    into a 500 on GET `/agent/{agent_id}` — the missing-agent answer must stay a 500."""
    response = await agents_client.get(agent_url(MISSING_ID))
    body = response.json()

    assert response.status_code == HTTP_INTERNAL_SERVER_ERROR
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.INTERNAL_ERROR.value
    assert body["error"]["error_id"]
    assert "Agent not found" not in body["detail"]  # the 404 text never surfaces


async def test_put_missing_agent_answers_the_swallowed_500(agents_client):
    """# legacy-parity(spec-0002): the PUT handler swallows its 404 the same way."""
    response = await agents_client.put(agent_url(MISSING_ID), json=CREATE_PAYLOAD)

    assert response.status_code == HTTP_INTERNAL_SERVER_ERROR
    assert response.json()["error"]["code"] == ErrorCode.INTERNAL_ERROR.value


async def test_delete_missing_agent_answers_the_swallowed_500(agents_client):
    """# legacy-parity(spec-0002): the DELETE handler swallows its 404 the same way."""
    response = await agents_client.delete(agent_url(MISSING_ID))

    assert response.status_code == HTTP_INTERNAL_SERVER_ERROR
    assert response.json()["error"]["code"] == ErrorCode.INTERNAL_ERROR.value


# --- prompts route (the one true 404) ------------------------------------------------------


async def test_prompts_round_trip(agents_client, agent_stores):
    """A stored prompt payload reads back byte-identical inside the envelope's data."""
    _definitions, prompt_store, _database = agent_stores
    await prompt_store.save_prompts(AGENT_ID, dict(PROMPTS))

    response = await agents_client.get(prompts_url(AGENT_ID))
    body = response.json()

    assert response.status_code == HTTP_OK
    assert body["ok"] is True
    assert body["data"] == {"agent_id": AGENT_ID, "agent_prompts": PROMPTS}


async def test_prompts_missing_payload_answers_null(agents_client):
    """No stored prompt payload degrades to `agent_prompts: null`, never an error."""
    response = await agents_client.get(prompts_url(AGENT_ID))

    assert response.status_code == HTTP_OK
    assert response.json()["data"] == {"agent_id": AGENT_ID, "agent_prompts": None}


async def test_prompts_missing_agent_is_a_true_404(agents_client):
    """# legacy-parity(spec-0002): the prompts handler alone re-raises its 404."""
    response = await agents_client.get(prompts_url(MISSING_ID))
    body = response.json()

    assert response.status_code == HTTP_NOT_FOUND
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.NOT_FOUND.value
    assert body["detail"] == "Agent not found"  # the legacy detail text, verbatim


# --- update + delete -----------------------------------------------------------------------


async def test_put_overwrites_and_injects_updated_status(agents_client, agent_stores):
    """PUT answers `{"agent_id", "state": "updated"}` and the store shows the new status."""
    definitions, _prompts, _database = agent_stores
    response = await agents_client.put(agent_url(AGENT_ID), json=CREATE_PAYLOAD)

    assert response.status_code == HTTP_OK
    assert response.json()["data"] == {"agent_id": AGENT_ID, "state": "updated"}
    stored = await definitions.get_agent(AGENT_ID)
    assert stored is not None and stored["assistant_status"] == "updated"  # legacy-parity


async def test_delete_removes_the_definition_and_its_prompts(agents_client, agent_stores):
    """T3 ends the orphan quirk: DELETE drops the definition and its payload together."""
    definitions, prompt_store, _database = agent_stores
    await prompt_store.save_prompts(AGENT_ID, dict(PROMPTS))

    response = await agents_client.delete(agent_url(AGENT_ID))

    assert response.status_code == HTTP_OK
    assert response.json()["data"] == {"agent_id": AGENT_ID, "state": "deleted"}
    assert await definitions.get_agent(AGENT_ID) is None
    assert await prompt_store.get_prompts(AGENT_ID) is None


# --- directory (/all) ----------------------------------------------------------------------


async def test_all_skips_non_agent_payloads_without_shifting_ids(agents_client, agent_stores):
    """The genuine-record filter survives without scans: non-agent configs are skipped
    while genuine records keep their own ids (never a positional zip)."""
    definitions, _prompts, _database = agent_stores
    await definitions.save_agent("bare-but-not-agent", dict(NON_AGENT_CONFIG))

    response = await agents_client.get(ALL_URL)
    body = response.json()

    assert response.status_code == HTTP_OK
    assert body["ok"] is True
    assert body["data"] == {"agents": [{"agent_id": AGENT_ID, "data": STORED_CONFIG}]}


async def test_responses_carry_the_correlation_id(agents_client):
    """Every envelope rides with the request id header that stitches it to the logs."""
    inbound = "req-agents-0001"

    response = await agents_client.get(ALL_URL, headers={REQUEST_ID_HEADER: inbound})

    assert response.headers[REQUEST_ID_HEADER] == inbound
    assert response.json()["meta"]["request_id"] == inbound


# --- unconfigured store --------------------------------------------------------------------


async def test_unconfigured_store_answers_503_not_a_crash(no_store_client):
    """With no definition store the service raises `DependencyUnavailableError` per call and
    the envelope answers a retryable 503 — the definition store is off, not broken."""
    response = await no_store_client.get(ALL_URL)
    body = response.json()

    assert response.status_code == HTTP_SERVICE_UNAVAILABLE
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE.value
    assert body["error"]["retryable"] is True
