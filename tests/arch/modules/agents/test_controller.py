"""The agents endpoints end to end: real app factory, real MODULE, ASGI transport (A4).

The only fakes go in through the composition seams the production wiring uses: a dict-backed
fake redis re-registered under the core `"redis"` container key, and the LEGACY
`voiceai.helpers.utils.PREPROCESS_DIR` pointed at a tmp dir (the same patch target the
quickstart tests use — monkeypatch transparency is part of what is under test). Every
response is the spec 0001 envelope with the quickstart payload byte-identical inside
`data`, and the legacy quirks are pinned by name: the 404-swallowed-to-500 on
GET/PUT/DELETE `/agent/{agent_id}`, the true 404 on the prompts route, falsy prompts as
null, and the prompt-file orphan on DELETE.
"""

from __future__ import annotations

import json

import pytest

import voiceai.helpers.utils as legacy_utils
from voiceai.common.constants import (
    API_PREFIX,
    CONTAINER_KEY_REDIS,
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_NOT_FOUND,
    HTTP_OK,
    HTTP_SERVICE_UNAVAILABLE,
    HTTP_UNPROCESSABLE_ENTITY,
    REQUEST_ID_HEADER,
)
from voiceai.common.errors import ErrorCode
from voiceai.modules.agents.constants import (
    AGENT_BY_ID_PATH,
    AGENT_PATH,
    AGENT_PROMPTS_PATH,
    ALL_AGENTS_PATH,
)

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
MISSING_ID = "00000000-0000-0000-0000-000000000000"
HTTP_CREATED = 201
PROMPT_FILE_NAME = "conversation_details.json"

AGENT_URL = f"{API_PREFIX}{AGENT_PATH}"
ALL_URL = f"{API_PREFIX}{ALL_AGENTS_PATH}"

STORED_CONFIG = {"agent_name": "Support", "agent_type": "other", "tasks": []}
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


class FakeRedis:
    """Dict-backed stand-in for the async redis client the repository consumes."""

    def __init__(self, records=None, wrongtype_keys=None):
        self.data = dict(records or {})
        self.wrongtype_keys = set(wrongtype_keys or set())

    async def get(self, name):
        """Raise for WRONGTYPE (set-typed) keys, else answer the stored string."""
        if name in self.wrongtype_keys:
            raise RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return self.data.get(name)

    async def set(self, name, value):
        """Store the raw string."""
        self.data[name] = value
        return True

    async def exists(self, *names):
        """Count stored keys among `names`."""
        return sum(1 for name in names if name in self.data)

    async def delete(self, *names):
        """Drop `names`; count removals."""
        return sum(1 for name in names if self.data.pop(name, None) is not None)

    async def keys(self, pattern):
        """Answer every key — stored and WRONGTYPE — like a `KEYS *` scan."""
        return list(self.data) + sorted(self.wrongtype_keys - set(self.data))

    async def aclose(self) -> None:
        """Match the shutdown contract the container calls on teardown."""


@pytest.fixture
def agents_redis():
    """A fake redis pre-seeded with one stored agent record under its bare-UUID key."""
    return FakeRedis({AGENT_ID: json.dumps(STORED_CONFIG)})


@pytest.fixture
def prompt_dir(tmp_path, monkeypatch):
    """Point the LEGACY `PREPROCESS_DIR` at a tmp dir (the real quickstart patch target)."""
    monkeypatch.setattr(legacy_utils, "PREPROCESS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
async def agents_client(arch_app, container_override, client_factory, agents_redis, prompt_dir):
    """The real app with the fake redis swapped in under the core `"redis"` key.

    `raise_app_exceptions=False` because half the pins below assert on 500 envelopes.
    """
    container_override(arch_app, CONTAINER_KEY_REDIS, agents_redis)
    async with client_factory(arch_app, raise_app_exceptions=False) as client:
        yield client


@pytest.fixture
async def no_redis_client(arch_app, client_factory, prompt_dir):
    """The real app with redis left unconfigured (`REDIS_URL` empty ⇒ client `None`)."""
    async with client_factory(arch_app, raise_app_exceptions=False) as client:
        yield client


def _write_prompt_file(root, agent_id, payload):
    directory = root / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PROMPT_FILE_NAME).write_text(json.dumps(payload))


# --- create + read back --------------------------------------------------------------------


async def test_create_then_read_round_trips_through_the_envelope(agents_client, agents_redis):
    """POST answers 201 with the quickstart `{"agent_id", "state"}` payload as data, and the
    stored record carries the seeded status readable back through GET `/agent/{id}`."""
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
    assert read_body["data"] == json.loads(agents_redis.data[agent_id])


async def test_create_writes_the_prompt_file_under_the_legacy_dir(agents_client, prompt_dir):
    """The prompt payload lands at `<agent_id>/conversation_details.json` via the legacy IO."""
    created = await agents_client.post(AGENT_URL, json=CREATE_PAYLOAD)
    agent_id = created.json()["data"]["agent_id"]

    stored = json.loads((prompt_dir / agent_id / PROMPT_FILE_NAME).read_text())

    assert stored == PROMPTS


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


async def test_prompts_round_trip(agents_client, prompt_dir):
    """A stored prompt file reads back byte-identical inside the envelope's data."""
    _write_prompt_file(prompt_dir, AGENT_ID, PROMPTS)

    response = await agents_client.get(prompts_url(AGENT_ID))
    body = response.json()

    assert response.status_code == HTTP_OK
    assert body["ok"] is True
    assert body["data"] == {"agent_id": AGENT_ID, "agent_prompts": PROMPTS}


async def test_prompts_missing_file_answers_null(agents_client):
    """No stored prompt file degrades to `agent_prompts: null`, never an error."""
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


async def test_put_overwrites_and_injects_updated_status(agents_client, agents_redis):
    """PUT answers `{"agent_id", "state": "updated"}` and the store shows the new status."""
    response = await agents_client.put(agent_url(AGENT_ID), json=CREATE_PAYLOAD)

    assert response.status_code == HTTP_OK
    assert response.json()["data"] == {"agent_id": AGENT_ID, "state": "updated"}
    assert json.loads(agents_redis.data[AGENT_ID])["assistant_status"] == "updated"  # legacy-parity


async def test_delete_removes_the_record_but_orphans_the_prompt_file(agents_client, agents_redis, prompt_dir):
    """# legacy-parity(spec-0002): DELETE drops the definition and leaves the prompt file."""
    _write_prompt_file(prompt_dir, AGENT_ID, PROMPTS)

    response = await agents_client.delete(agent_url(AGENT_ID))

    assert response.status_code == HTTP_OK
    assert response.json()["data"] == {"agent_id": AGENT_ID, "state": "deleted"}
    assert AGENT_ID not in agents_redis.data
    assert (prompt_dir / AGENT_ID / PROMPT_FILE_NAME).exists()  # deliberately orphaned


# --- directory (/all) ----------------------------------------------------------------------


async def test_all_skips_namespaced_keys_and_non_agent_payloads(agents_client, agents_redis):
    """The `KEYS *` scan quirks survive: `":"`-keys, WRONGTYPE keys, and non-agent JSON are
    skipped while genuine records keep their own ids (never a positional zip)."""
    agents_redis.data["platform:index"] = "not-an-agent"
    agents_redis.data["bare-but-not-agent"] = json.dumps({"no": "tasks"})
    agents_redis.wrongtype_keys.add("platform:sessions")

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


# --- unconfigured redis --------------------------------------------------------------------


async def test_unconfigured_redis_answers_503_not_a_crash(no_redis_client):
    """With `REDIS_URL` empty the service raises `DependencyUnavailableError` per call and
    the envelope answers a retryable 503 — the definition store is off, not broken."""
    response = await no_redis_client.get(ALL_URL)
    body = response.json()

    assert response.status_code == HTTP_SERVICE_UNAVAILABLE
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE.value
    assert body["error"]["retryable"] is True
