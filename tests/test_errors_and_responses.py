"""Guards the error contract: codes map to HTTP/WS statuses, envelopes hide internals, facades keep signatures."""

import asyncio
import logging

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocketState

from voiceai.errors import (
    AgentNotFoundError,
    ConfigurationError,
    ErrorCode,
    InternalError,
    LLMError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    VoiceAIError,
    classify_exception,
)
from voiceai.exceptions import LLMError as FacadeLLMError
from voiceai.exceptions import SynthesizerError, TranscriberError, VoiceAIComponentError
from voiceai.responses import ErrorEnvelope, close_with_error, register_exception_handlers, ws_error_frame

# -- voiceai.errors ------------------------------------------------------------------------


def test_every_error_code_maps_to_http_and_ws_codes():
    for code in ErrorCode:
        err = VoiceAIError("x", code=code)
        assert 400 <= err.http_status <= 599, code
        assert 4000 <= err.ws_close_code <= 4999, code
        # One client-side table serves both transports: the close code is 4000 + the HTTP status.
        assert err.ws_close_code == 4000 + err.http_status, code
    assert AgentNotFoundError("ghost").http_status == 404
    assert ProviderTimeoutError("slow").http_status == 504
    assert InternalError("oops").ws_close_code == 4500


def test_to_dict_carries_identity_and_optional_context():
    plain = VoiceAIError("boom", code=ErrorCode.CONFLICT, component="api", retryable=True)
    assert plain.to_dict() == {
        "code": "conflict",
        "message": "boom",
        "error_id": plain.error_id,
        "retryable": True,
        "component": "api",
    }
    assert plain.error_id and str(plain) == "boom"
    assert VoiceAIError("a").error_id != VoiceAIError("a").error_id

    rich = LLMError("rate limited", provider="openai", model="gpt-4o", details={"status": 429})
    body = rich.to_dict()
    assert body["component"] == "llm"
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o"
    assert body["details"] == {"status": 429}
    assert rich.with_context(turn=3, skipped=None).to_dict()["details"] == {"status": 429, "turn": 3}


def test_internal_error_hides_message_but_keeps_reference():
    err = InternalError("db password=hunter2")
    assert "hunter2" not in err.public_message
    assert err.error_id in err.public_message
    assert str(err) == "db password=hunter2"  # the log line keeps the precise text
    assert err.to_dict()["message"] == err.public_message


class _Synthesizer(BaseModel):
    provider: str
    voice: str


class _ToolsConfig(BaseModel):
    synthesizer: _Synthesizer


def test_configuration_error_from_validation_error_builds_readable_paths():
    with pytest.raises(ValidationError) as caught:
        _ToolsConfig(synthesizer={"voice": 42})
    err = ConfigurationError.from_validation_error(caught.value, prefix="tasks[0].tools_config")

    paths = {issue["path"] for issue in err.issues}
    assert paths == {"tasks[0].tools_config.synthesizer.provider", "tasks[0].tools_config.synthesizer.voice"}
    assert all(issue["message"] and issue["type"] for issue in err.issues)
    assert err.code is ErrorCode.CONFIGURATION_INVALID and err.http_status == 400
    assert err.path in paths and err.message.startswith(err.path)
    assert "(+1 more)" in err.message
    assert err.details == {"path": err.path, "issues": err.issues}
    assert err.__cause__ is caught.value
    # classify_exception recognises pydantic errors without importing pydantic itself.
    assert isinstance(classify_exception(caught.value), ConfigurationError)


def test_classify_exception_maps_stdlib_failures_and_passes_ours_through():
    timeout = classify_exception(asyncio.TimeoutError(), component="llm", provider="openai", model="gpt-4o")
    assert isinstance(timeout, ProviderTimeoutError) and timeout.retryable
    assert (timeout.component, timeout.provider, timeout.model) == ("llm", "openai", "gpt-4o")

    reset = classify_exception(ConnectionResetError("peer went away"), component="transcriber", provider="deepgram")
    assert isinstance(reset, ProviderConnectionError) and reset.retryable
    assert reset.details["exception"] == "ConnectionResetError"
    assert isinstance(reset.__cause__, ConnectionResetError)

    ours = LLMError("bad response", model="gpt-4o")
    same = classify_exception(ours, provider="openai", model="ignored")
    assert same is ours
    assert (same.provider, same.model) == ("openai", "gpt-4o")  # only fills what was missing

    unknown = classify_exception(KeyError("turn_id"))
    assert isinstance(unknown, InternalError) and unknown.http_status == 500
    assert "KeyError" in unknown.message and unknown.error_id in unknown.public_message

    in_component = classify_exception(RuntimeError("synth exploded"), component="synthesizer", provider="cartesia")
    assert type(in_component) is ProviderError
    assert (in_component.code, in_component.component, in_component.provider) == (
        ErrorCode.PROVIDER_ERROR,
        "synthesizer",
        "cartesia",
    )


def test_exceptions_facade_keeps_historical_signatures():
    component = VoiceAIComponentError("trunk down", "telephony", "twilio")
    assert (component.component, component.provider, component.model) == ("telephony", "twilio", None)
    assert str(component) == "trunk down"

    llm = FacadeLLMError("bad completion", "openai", "gpt-4o")
    assert isinstance(llm, VoiceAIComponentError) and isinstance(llm, VoiceAIError)
    assert (llm.component, llm.provider, llm.model) == ("llm", "openai", "gpt-4o")
    assert FacadeLLMError is LLMError  # the facade and voiceai.errors share one class

    assert SynthesizerError("m", provider="cartesia").component == "synthesizer"
    assert TranscriberError("m", model="nova-3").component == "transcriber"
    assert TranscriberError("m").provider is None


# -- voiceai.responses: HTTP ---------------------------------------------------------------


class _Payload(BaseModel):
    count: int


def _build_app(logger):
    app = FastAPI()
    register_exception_handlers(app, logger=logger)

    @app.get("/agents/{agent_id}")
    async def agent(agent_id: str):
        raise AgentNotFoundError(agent_id)

    @app.get("/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="nope")

    @app.get("/challenge")
    async def challenge():
        raise HTTPException(status_code=401, detail="who?", headers={"WWW-Authenticate": "Bearer"})

    @app.post("/validate")
    async def validate(payload: _Payload):
        return {"count": payload.count}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("db password=hunter2")

    return app


@pytest_asyncio.fixture
async def api():
    # raise_app_exceptions=False: Starlette re-raises an unhandled exception after the 500
    # handler has sent its response (as a real server does); the test wants that response.
    transport = ASGITransport(app=_build_app(logging.getLogger("tests.responses")), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _envelope(resp):
    body = resp.json()
    ErrorEnvelope.model_validate(body)  # the documented wire shape
    assert body["ok"] is False
    assert body["detail"] == body["error"]["message"]
    assert body["error"]["error_id"]
    return body


async def test_voiceai_error_renders_envelope_with_its_status(api):
    resp = await api.get("/agents/ghost")
    assert resp.status_code == 404
    body = _envelope(resp)
    assert body["error"]["code"] == "agent_not_found"
    assert body["detail"] == "Agent not found: ghost"
    assert body["error"]["details"] == {"resource": "agent", "id": "ghost"}


async def test_legacy_http_exception_gets_the_same_envelope(api):
    resp = await api.get("/forbidden")
    assert resp.status_code == 403
    body = _envelope(resp)
    assert body["detail"] == "nope"
    assert body["error"]["code"] == "forbidden"

    challenge = await api.get("/challenge")
    assert challenge.status_code == 401
    assert challenge.headers["www-authenticate"] == "Bearer"  # auth headers survive the rewrap
    assert _envelope(challenge)["error"]["code"] == "unauthenticated"


async def test_request_validation_lists_each_issue(api):
    resp = await api.post("/validate", json={"count": "many"})
    assert resp.status_code == 422
    body = _envelope(resp)
    assert body["error"]["code"] == "validation_failed"
    issues = body["error"]["details"]["errors"]
    assert isinstance(issues, list) and issues
    assert issues[0]["loc"] == ["body", "count"]
    assert "count" in body["detail"]

    ok = await api.post("/validate", json={"count": 3})
    assert ok.status_code == 200 and ok.json() == {"count": 3}


async def test_unexpected_exception_hides_internals_but_logs_them(api, caplog):
    with caplog.at_level(logging.ERROR, logger="tests.responses"):
        resp = await api.get("/boom")
    assert resp.status_code == 500
    body = _envelope(resp)
    error_id = body["error"]["error_id"]
    assert "hunter2" not in resp.text
    assert error_id in body["detail"]
    assert body["error"]["code"] == "internal_error"
    # Operators can still find the real failure: the log line carries both.
    assert "hunter2" in caplog.text and error_id in caplog.text


# -- voiceai.responses: WebSocket ----------------------------------------------------------


class _FakeSocket:
    """Just enough of starlette's WebSocket for close_with_error: state, send_json, close."""

    def __init__(self, *, connected=True, send_fails=False, close_fails=False):
        self.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
        self.events = []
        self._send_fails = send_fails
        self._close_fails = close_fails

    async def send_json(self, payload):
        if self._send_fails:
            raise RuntimeError("socket already gone")
        self.events.append(("send", payload))

    async def close(self, code=1000, reason=None):
        if self._close_fails:
            raise RuntimeError("close raced the peer")
        self.events.append(("close", code, reason))


def test_ws_error_frame_is_typed_and_carries_the_envelope():
    err = ProviderTimeoutError("llm timed out", component="llm", provider="openai")
    frame = ws_error_frame(err)
    assert frame["type"] == "error" and frame["ok"] is False
    assert frame["detail"] == "llm timed out"
    assert frame["error"]["code"] == "provider_timeout" and frame["error"]["retryable"] is True
    ErrorEnvelope.model_validate({key: value for key, value in frame.items() if key != "type"})


async def test_close_with_error_sends_frame_then_closes_with_mapped_code():
    err = AgentNotFoundError("ghost")
    socket = _FakeSocket()
    await close_with_error(socket, err)
    assert socket.events[0] == ("send", ws_error_frame(err))
    kind, code, reason = socket.events[1]
    assert kind == "close" and code == err.ws_close_code == 4404
    assert reason.startswith("agent_not_found: ")
    assert len(socket.events) == 2


async def test_close_with_error_never_raises_and_still_closes():
    err = VoiceAIError("x" * 500)

    broken_send = _FakeSocket(send_fails=True)
    await close_with_error(broken_send, err)
    assert [event[0] for event in broken_send.events] == ["close"]
    assert broken_send.events[0][1] == err.ws_close_code

    broken_both = _FakeSocket(send_fails=True, close_fails=True)
    await close_with_error(broken_both, err)  # must not raise
    assert broken_both.events == []

    gone = _FakeSocket(connected=False)
    await close_with_error(gone, err)
    assert gone.events == []

    quiet = _FakeSocket()
    await close_with_error(quiet, err, send_frame=False)
    assert [event[0] for event in quiet.events] == ["close"]


async def test_close_reason_fits_the_rfc6455_cap():
    socket = _FakeSocket()
    await close_with_error(socket, VoiceAIError("x" * 500), send_frame=False)
    _, _, reason = socket.events[0]
    assert len(reason.encode("utf-8")) <= 123
    assert reason.endswith("…")

    # Multibyte text near the cut must not produce a broken character or overshoot.
    wide = _FakeSocket()
    await close_with_error(wide, VoiceAIError("नमस्ते " * 60), send_frame=False)
    _, _, wide_reason = wide.events[0]
    assert len(wide_reason.encode("utf-8")) <= 123
