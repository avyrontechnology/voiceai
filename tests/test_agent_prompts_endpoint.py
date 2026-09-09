"""Contract tests for GET /agent/{agent_id}/prompts (stored prompts read-back, behind agents:read)."""

import json
import os

import pytest
import pytest_asyncio

# The quickstart server pulls the full engine dependency chain (numpy, ...),
# which the minimal platform-test env does not install. Skip there instead
# of failing collection; the endpoint is exercised in full backend envs.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
pytest.importorskip("numpy")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import voiceai.helpers.utils as utils  # noqa: E402
from local_setup import quickstart_server as server  # noqa: E402
from tests.auth_helpers import signup_owner  # noqa: E402
from voiceai.platform.store import MemoryStore  # noqa: E402


class _FakeRedis:
    def __init__(self, records):
        self._records = records

    async def get(self, key):
        record = self._records.get(key)
        return json.dumps(record) if record is not None else None


def _client():
    return AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test")


@pytest_asyncio.fixture
async def prompts_client(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "PREPROCESS_DIR", str(tmp_path))
    monkeypatch.setattr(
        server,
        "redis_client",
        _FakeRedis({"agent-1": {"agent_name": "Support", "agent_type": "s2s", "tasks": []}}),
    )
    # The route requires agents:read. The app boots with RedisStore(redis_client); swap in an
    # in-memory store so signup and the session lookup never touch Redis. The auth dependency
    # reads the store from app.state on every request, so the swap applies immediately.
    monkeypatch.setattr(server.app.state, "platform_store", MemoryStore(), raising=False)
    async with _client() as client:
        await signup_owner(client)  # the owner session cookie carries every scope
        yield client


def _write_prompts(root, agent_id, payload):
    directory = root / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conversation_details.json").write_text(json.dumps(payload))


async def test_prompts_roundtrip(prompts_client, tmp_path):
    stored = {"task_1": {"system_prompt": "You are support.", "multilingual_prompts": {}}}
    _write_prompts(tmp_path, "agent-1", stored)

    resp = await prompts_client.get("/agent/agent-1/prompts")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_id"] == "agent-1"
    assert body["agent_prompts"] == stored


async def test_prompts_missing_file_returns_null(prompts_client):
    resp = await prompts_client.get("/agent/agent-1/prompts")

    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_prompts"] is None


async def test_prompts_missing_agent_returns_404(prompts_client):
    resp = await prompts_client.get("/agent/nope/prompts")

    assert resp.status_code == 404
    body = resp.json()
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["error"]["code"] == "agent_not_found"
    assert body["error"]["error_id"]


async def test_prompts_require_a_session(prompts_client):
    async with _client() as anonymous:
        resp = await anonymous.get("/agent/agent-1/prompts")

    assert resp.status_code == 401
