"""Guards /chat/v1/{agent_id}: every failure sends an error frame and a mapped 4xxx close code."""

import asyncio
import json
import os

import pytest

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
pytest.importorskip("numpy")

from local_setup import quickstart_server as server  # noqa: E402
from voiceai.errors import ConfigurationError  # noqa: E402
from voiceai.platform.stream_token import mint_stream_token  # noqa: E402
from voiceai.platform.store import MemoryStore  # noqa: E402

SECRET = "unit-test-stream-secret-0123456789"

AGENT = {
    "agent_name": "Support",
    "tasks": [
        {
            "task_type": "conversation",
            "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm", "synthesizer"]]},
            "tools_config": {
                "input": {"provider": "twilio", "format": "wav"},
                "output": {"provider": "twilio", "format": "wav"},
                "transcriber": {"provider": "deepgram", "model": "nova-2"},
                "llm_agent": {
                    "agent_type": "simple_llm_agent",
                    "agent_flow_type": "streaming",
                    "llm_config": {"provider": "openai"},
                },
                "synthesizer": {"provider": "elevenlabs", "provider_config": {"voice": "v", "voice_id": "id"}},
            },
        }
    ],
}


class _FakeRedis:
    def __init__(self, records):
        self.records = records

    async def get(self, key):
        record = self.records.get(key)
        return json.dumps(record) if record is not None else None


class _FakeManager:
    """Stands in for AssistantManager: sends one frame, yields one output, or raises."""

    instances = []
    raise_with = None

    def __init__(self, agent_config, websocket, agent_id, is_web_based_call=False):
        self.agent_config = agent_config
        self.websocket = websocket
        self.agent_id = agent_id
        self.is_web_based_call = is_web_based_call
        self.run_id = "run-1"
        _FakeManager.instances.append(self)

    async def run(self, local=False):
        if _FakeManager.raise_with is not None:
            raise _FakeManager.raise_with
        await self.websocket.send_json({"type": "hello", "agent": self.agent_id, "browser": self.is_web_based_call})
        yield 0, {"messages": [{"role": "assistant", "content": "hi"}], "run_id": self.run_id}


class Closed(Exception):
    def __init__(self, code):
        super().__init__(f"closed with {code}")
        self.code = code


class _WsSession:
    """Minimal in-process ASGI websocket driver (the pinned Starlette TestClient rejects new httpx)."""

    def __init__(self, app, path, query=""):
        self.app = app
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "subprotocols": [],
        }
        self._to_app = asyncio.Queue()
        self._from_app = asyncio.Queue()
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.app(self.scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._next()
        assert first["type"] == "websocket.accept", first
        return self

    async def _next(self, timeout=5.0):
        getter = asyncio.ensure_future(self._from_app.get())
        done, _ = await asyncio.wait({getter, self.task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            return getter.result()
        getter.cancel()
        if self.task in done:
            self.task.result()  # surface an app crash instead of a bare timeout
            raise Closed(None)
        raise AssertionError("no websocket message within timeout")

    async def receive_json(self):
        message = await self._next()
        if message["type"] == "websocket.close":
            raise Closed(message.get("code"))
        return json.loads(message["text"])

    async def __aexit__(self, *exc):
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self.task, timeout=5.0)
        except (Closed, asyncio.CancelledError):
            pass


@pytest.fixture
def ws(monkeypatch):
    monkeypatch.setenv("VOICE_STREAM_SECRET", SECRET)
    monkeypatch.setattr(server, "redis_client", _FakeRedis({"agent-1": AGENT}))
    monkeypatch.setattr(server, "AssistantManager", _FakeManager)
    server.app.state.platform_store = MemoryStore()
    _FakeManager.instances.clear()
    _FakeManager.raise_with = None

    def connect(path, query=""):
        return _WsSession(server.app, path, query)

    return connect


async def _expect_error_close(session, code):
    frame = await session.receive_json()
    assert frame["type"] == "error"
    assert frame["ok"] is False
    assert frame["detail"] == frame["error"]["message"]
    with pytest.raises(Closed) as info:
        await session.receive_json()
    assert info.value.code == code
    return frame["error"]


async def test_no_credentials_is_refused_with_4401_and_a_hint(ws):
    async with ws("/chat/v1/agent-1") as session:
        error = await _expect_error_close(session, 4401)
    assert error["code"] == "unauthenticated"
    assert "signed stream token" in error["message"]
    assert not _FakeManager.instances
    assert server.active_websockets == []


async def test_signed_stream_token_admits_a_carrier_leg(ws):
    token = mint_stream_token("agent-1", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}") as session:
        assert await session.receive_json() == {"type": "hello", "agent": "agent-1", "browser": False}
    assert len(_FakeManager.instances) == 1
    assert server.active_websockets == []


async def test_token_for_another_agent_is_refused(ws):
    token = mint_stream_token("agent-2", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}") as session:
        await _expect_error_close(session, 4401)


async def test_unknown_agent_closes_with_4404(ws):
    token = mint_stream_token("nope", ttl_s=60)
    async with ws("/chat/v1/nope", f"token={token}") as session:
        error = await _expect_error_close(session, 4404)
    assert error["code"] == "agent_not_found"


async def test_invalid_stored_config_closes_with_4400_and_a_path(ws, monkeypatch):
    broken = json.loads(json.dumps(AGENT))
    broken["tasks"][0]["tools_config"]["synthesizer"]["provider"] = "not-a-tts"
    monkeypatch.setattr(server, "redis_client", _FakeRedis({"agent-1": broken}))
    token = mint_stream_token("agent-1", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}") as session:
        error = await _expect_error_close(session, 4400)
    assert error["code"] == "configuration_invalid"
    assert error["details"]["path"] == "tasks[0].tools_config.synthesizer.provider"
    assert not _FakeManager.instances


async def test_engine_configuration_error_reaches_the_client(ws):
    _FakeManager.raise_with = ConfigurationError(
        "voice 'x' is unknown", path="tasks[0].tools_config.synthesizer.provider_config.voice"
    )
    token = mint_stream_token("agent-1", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}") as session:
        error = await _expect_error_close(session, 4400)
    assert error["message"] == "voice 'x' is unknown"


async def test_unexpected_engine_exception_is_not_leaked(ws):
    _FakeManager.raise_with = RuntimeError("redis password=hunter2 exploded")
    token = mint_stream_token("agent-1", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}") as session:
        error = await _expect_error_close(session, 4500)
    assert error["code"] == "internal_error"
    assert "hunter2" not in json.dumps(error)
    assert error["error_id"] in error["message"]
    assert server.active_websockets == []


async def test_browser_leg_runs_on_default_handlers(ws):
    token = mint_stream_token("agent-1", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}&leg=browser") as session:
        assert (await session.receive_json())["browser"] is True
    manager = _FakeManager.instances[0]
    io = manager.agent_config["tasks"][0]["tools_config"]
    assert io["input"]["provider"] == "default" and io["output"]["provider"] == "default"
    assert AGENT["tasks"][0]["tools_config"]["input"]["provider"] == "twilio"  # stored config untouched


async def test_execution_is_recorded_for_the_platform(ws):
    token = mint_stream_token("agent-1", ttl_s=60)
    async with ws("/chat/v1/agent-1", f"token={token}") as session:
        await session.receive_json()
    executions = await server.app.state.platform_store.list_executions()
    assert [e.agent_id for e in executions] == ["agent-1"]
