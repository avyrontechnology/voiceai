"""Model-emitted tool arguments must never overwrite the configured outbound request.

``FunctionCallPayload`` allows extras, and every provider used to copy each parsed argument onto it
with ``setattr``. The task manager then splats that payload into ``trigger_api(url=..., api_token=...,
**resp)``, so a caller who steers the model into emitting a ``url`` or ``api_token`` argument
redirected the tool's HTTP call (an SSRF to the cloud metadata endpoint) and re-authenticated it with
a token of their choosing. Every path that applies arguments must go through
``apply_tool_arguments``, which drops reserved keys and keeps them out of ``**resp`` too.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.llms.gemini_llm import GeminiLLM
from voiceai.llms.openai_llm import OpenAiLLM
from voiceai.llms.tool_call_accumulator import ToolCallAccumulator
from voiceai.llms.types import (
    RESERVED_TOOL_ARGUMENT_KEYS,
    FunctionCallPayload,
    apply_tool_arguments,
    redact_secrets,
)

# What a hostile caller gets the model to emit: the engine's own fields smuggled in as tool
# arguments, alongside one legitimate one that must still land.
HOSTILE_ARGS = {"url": "http://169.254.169.254/", "api_token": "x", "customer_id": "42"}

CONFIGURED_URL = "https://example.test/book"
CONFIGURED_TOKEN = "configured-token"
API_PARAMS = {"book": {"url": CONFIGURED_URL, "method": "POST", "api_token": CONFIGURED_TOKEN, "param": None}}

# The tool spec, in each path's own shape. All three require customer_id so the success branch runs.
PARAMETERS = {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}
CHAT_TOOLS = [{"function": {"name": "book", "description": "book it", "parameters": PARAMETERS}}]
RESPONSES_TOOLS = [{"name": "book", "description": "book it", "parameters": PARAMETERS}]
GEMINI_TOOLS = [{"type": "function", "function": {"name": "book", "description": "book it", "parameters": PARAMETERS}}]

# TaskManager.__execute_function_call names these explicitly; everything else in the payload lands in
# its ``**resp``, which it forwards to trigger_api. Keep in sync with that signature.
_NAMED_EXECUTE_PARAMS = ("url", "method", "param", "api_token", "headers", "model_args", "meta_info", "called_fun")


def _resp_kwargs(payload):
    """What lands in ``**resp`` when the task manager splats this payload."""
    return {k: v for k, v in payload.model_dump().items() if k not in _NAMED_EXECUTE_PARAMS}


def _assert_guarded(payload):
    """The three invariants every path must hold after applying HOSTILE_ARGS."""
    assert payload.url == CONFIGURED_URL
    assert payload.api_token == CONFIGURED_TOKEN
    assert payload.customer_id == "42"

    resp = _resp_kwargs(payload)
    assert "url" not in resp
    assert "api_token" not in resp
    assert resp["customer_id"] == "42"
    # The splat would otherwise pass url/api_token twice; nothing reserved may survive in resp.
    assert not (
        RESERVED_TOOL_ARGUMENT_KEYS & set(resp) - {"model_response", "tool_call_id", "textual_response", "resp"}
    )


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def _payload():
    return FunctionCallPayload(
        url=CONFIGURED_URL, method="post", api_token=CONFIGURED_TOKEN, called_fun="book", tool_call_id="call-1"
    )


def test_reserved_keys_cover_every_declared_payload_field():
    assert set(FunctionCallPayload.model_fields) <= RESERVED_TOOL_ARGUMENT_KEYS


def test_helper_returns_only_the_arguments_it_kept():
    kept = apply_tool_arguments(_payload(), HOSTILE_ARGS)
    assert kept == {"customer_id": "42"}


def test_helper_warns_once_naming_the_rejected_keys():
    logger = MagicMock()
    apply_tool_arguments(_payload(), HOSTILE_ARGS, logger=logger)
    warnings = [call.args[0] for call in logger.warning.call_args_list]
    assert len(warnings) == 1
    assert "'api_token'" in warnings[0] and "'url'" in warnings[0]


def test_engine_owned_kwargs_are_reserved_too():
    """A tool arg named like a trigger_api parameter would make the splat raise, killing the call."""
    payload = _payload()
    apply_tool_arguments(payload, {"headers_data": {}, "run_id": "x", "next_step": "y", "ok": 1})
    assert _resp_kwargs(payload).keys() & {"headers_data", "run_id", "next_step"} == set()
    assert payload.ok == 1


def test_class_attribute_names_are_never_shadowed():
    """pydantic puts these on the instance, not in extras, breaking every later model_dump()."""
    payload = _payload()
    apply_tool_arguments(payload, {"model_dump": "boom", "copy": "boom", "_private": "boom", "kept": "yes"})
    assert callable(payload.model_dump)
    assert payload.model_dump()["called_fun"] == "book"
    assert payload.kept == "yes"


def test_non_object_arguments_are_ignored():
    payload = _payload()
    assert apply_tool_arguments(payload, ["not", "an", "object"]) == {}
    assert payload.url == CONFIGURED_URL


# ---------------------------------------------------------------------------
# Chat completions: ToolCallAccumulator (OpenAI, Azure, LiteLLM)
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_request_logging():
    """The success path logs the call, which needs a live loop and is not what these tests pin."""
    with (
        patch("voiceai.llms.tool_call_accumulator.convert_to_request_log"),
        patch("voiceai.llms.openai_base.convert_to_request_log"),
        patch("voiceai.llms.gemini_llm.convert_to_request_log"),
    ):
        yield


def test_chat_accumulator_keeps_the_configured_request(_no_request_logging):
    acc = ToolCallAccumulator(API_PARAMS, CHAT_TOOLS, "en", "gpt-4o", "run-1")
    acc.process_delta(
        [
            SimpleNamespace(
                index=0, id="call-1", function=SimpleNamespace(name="book", arguments=json.dumps(HOSTILE_ARGS))
            )
        ]
    )
    _assert_guarded(acc.build_api_payload({"model": "gpt-4o"}, {"request_id": "q"}, ""))


# ---------------------------------------------------------------------------
# Responses API: OpenAICompatibleLLM._build_function_call_chunk / text-tool-call rescue
# ---------------------------------------------------------------------------


def _openai_llm():
    with patch.object(OpenAiLLM, "__init__", lambda self, **kw: None):
        llm = OpenAiLLM.__new__(OpenAiLLM)
    llm.model = "gpt-4o"
    llm.run_id = "run-1"
    llm.trigger_function_call = True
    llm.api_params = API_PARAMS
    llm.tools = CHAT_TOOLS
    return llm


def test_responses_path_keeps_the_configured_request(_no_request_logging):
    chunk = _openai_llm()._build_function_call_chunk(
        func_call_args={"item-1": json.dumps(HOSTILE_ARGS)},
        func_call_names={"item-1": "book"},
        func_call_ids={"item-1": "call-1"},
        responses_tools=RESPONSES_TOOLS,
        create_kwargs={"model": "gpt-4o"},
        meta_info={"request_id": "q"},
        answer="",
        received_textual=False,
        latency_data=None,
    )
    assert chunk.is_function_call
    _assert_guarded(chunk.data)


def test_text_tool_call_rescue_keeps_the_configured_request(_no_request_logging):
    chunk = _openai_llm()._try_rescue_text_tool_call(
        f"functions.book({json.dumps(HOSTILE_ARGS)})",
        {"tools": CHAT_TOOLS},
        {"request_id": "q"},
        "",
        None,
    )
    assert chunk is not None and chunk.is_function_call
    _assert_guarded(chunk.data)


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _gemini_function_call_chunk(args):
    part = SimpleNamespace(
        thought=False,
        thought_signature=None,
        text=None,
        function_call=SimpleNamespace(name="book", id="call-1", args=dict(args)),
    )
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(usage_metadata=None, candidates=[SimpleNamespace(content=content)], text=None)


async def test_gemini_path_keeps_the_configured_request(_no_request_logging):
    llm = GeminiLLM(
        model="gemini-2.5-flash",
        llm_key="test-key",
        run_id="run-1",
        api_tools={"tools_params": API_PARAMS, "tools": GEMINI_TOOLS},
    )
    stream = _FakeStream([_gemini_function_call_chunk(HOSTILE_ARGS)])
    with patch.object(llm.client.aio.models, "generate_content_stream", return_value=stream, create=True):
        chunks = [c async for c in llm.generate_stream([{"role": "user", "content": "book me in"}], meta_info={})]

    payloads = [c.data for c in chunks if c.is_function_call]
    assert len(payloads) == 1
    _assert_guarded(payloads[0])


# ---------------------------------------------------------------------------
# Secrets in logs
# ---------------------------------------------------------------------------


def test_redact_secrets_hides_tool_credentials():
    redacted = redact_secrets({"url": CONFIGURED_URL, "api_token": CONFIGURED_TOKEN, "method": "POST"})
    assert redacted == {"url": CONFIGURED_URL, "api_token": "***", "method": "POST"}


def test_redact_secrets_walks_nested_bags_and_json_headers():
    redacted = redact_secrets(
        {
            "model_args": {"api_key": "sk-live", "model": "gpt-4o"},
            "headers": json.dumps({"Authorization": "Bearer live", "X-Trace": "keep"}),
            "tools": [{"api_token": "t"}],
        }
    )
    assert redacted["model_args"] == {"api_key": "***", "model": "gpt-4o"}
    assert redacted["headers"] == {"Authorization": "***", "X-Trace": "keep"}
    assert redacted["tools"] == [{"api_token": "***"}]


def test_redact_secrets_accepts_pydantic_models():
    payload = FunctionCallPayload(url=CONFIGURED_URL, api_token=CONFIGURED_TOKEN)
    assert redact_secrets(payload)["api_token"] == "***"


# ---------------------------------------------------------------------------
# S2S: model args must never collide with trigger_api's own kwargs
# ---------------------------------------------------------------------------


def _make_s2s_tm(tool_params: dict[str, Any]) -> Any:
    """Minimal TaskManager stub for _s2s_call_api_tool (no media loops, no auth)."""
    from voiceai.agent_manager.task_manager import TaskManager

    tm = TaskManager.__new__(TaskManager)
    tm.run_id = "run-1"
    tm.function_tool_api_call_details = []
    return tm


async def test_s2s_strips_reserved_args_before_trigger_api() -> None:
    """Hostile url/api_token in S2S args must not collide with trigger_api kwargs."""
    from voiceai.s2s import events as s2s_events

    tm = _make_s2s_tm({"book": {"url": CONFIGURED_URL}})
    hostile = {"customer_id": "42", "url": "http://169.254.169.254/", "api_token": "x"}
    hostile.update({"headers_data": {}, "run_id": "evil", "return_response_metadata": True})
    event = s2s_events.FunctionCall(name="book", call_id="c1", arguments=json.dumps(hostile))
    params = {"url": CONFIGURED_URL, "method": "POST", "api_token": CONFIGURED_TOKEN, "param": None}

    with patch(
        "voiceai.agent_manager.task_manager.trigger_api",
        new=AsyncMock(return_value={"body": '{"ok":1}', "status_code": 200}),
    ) as api:
        result = await tm._s2s_call_api_tool(event, hostile, params, {"request_id": "q"})

    assert result == '{"ok":1}'
    assert api.await_args.kwargs["url"] == CONFIGURED_URL
    # Reserved keys never reach the splat, so trigger_api sees no duplicate kwarg.
    assert api.await_args.kwargs["customer_id"] == "42"
    # The configured request wins; hostile values are dropped, not forwarded.
    assert api.await_args.kwargs["api_token"] == CONFIGURED_TOKEN
    assert api.await_args.kwargs["headers_data"] is None
    assert api.await_args.kwargs["run_id"] == "run-1"
    assert api.await_args.kwargs["return_response_metadata"] is True


async def test_s2s_non_dict_args_do_not_collide() -> None:
    """A non-object S2S arguments payload must not break the tool call."""
    from voiceai.s2s import events as s2s_events

    tm = _make_s2s_tm({"book": {"url": CONFIGURED_URL}})
    event = s2s_events.FunctionCall(name="book", call_id="c1", arguments="[]")
    params = {"url": CONFIGURED_URL, "method": "POST", "api_token": CONFIGURED_TOKEN, "param": None}

    with patch(
        "voiceai.agent_manager.task_manager.trigger_api",
        new=AsyncMock(return_value={"body": '{"ok":1}', "status_code": 200}),
    ) as api:
        result = await tm._s2s_call_api_tool(event, ["not", "an", "object"], params, {"request_id": "q"})

    assert result == '{"ok":1}'
    assert api.await_args.kwargs["url"] == CONFIGURED_URL


# ---------------------------------------------------------------------------
# Server-owned ids must never reach the model (all providers)
# ---------------------------------------------------------------------------


def _server_id_tool(name: str = "book") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "d",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "call_sid": {"type": "string"},
                    "stream_sid": {"type": "string"},
                },
                "required": ["customer_id", "call_sid"],
            },
        },
    }


def _tool_names(tools: Any) -> list[str]:
    return [(t.get("function") or {}).get("name") for t in tools]


async def test_litellm_strips_server_injected_params() -> None:
    """LiteLLM must drop call_sid/stream_sid from the tools sent on the wire."""
    from voiceai.llms.litellm import LiteLLM

    llm = LiteLLM(
        model="gpt-4o-mini",
        api_tools={"tools": [_server_id_tool()], "tools_params": {"book": {"url": CONFIGURED_URL}}},
    )
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise _SentinelForGuard()

    class _SentinelForGuard(Exception):
        pass

    with patch("voiceai.llms.litellm.acompletion", fake_acompletion):
        with pytest.raises(_SentinelForGuard):
            async for _ in llm.generate_stream([{"role": "user", "content": "hi"}]):
                pass
    props = captured["tools"][0]["function"]["parameters"]["properties"]
    assert "call_sid" not in props and "stream_sid" not in props
    assert "customer_id" in props
    assert "call_sid" not in captured["tools"][0]["function"]["parameters"].get("required", [])


async def test_litellm_per_turn_tools_override_is_stripped() -> None:
    """A per-node tools= override on LiteLLM must be stripped the same way."""
    from voiceai.llms.litellm import LiteLLM

    llm = LiteLLM(
        model="gpt-4o-mini",
        api_tools={"tools": [_server_id_tool("a")], "tools_params": {"a": {"url": CONFIGURED_URL}}},
    )
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise _SentinelOverride()

    class _SentinelOverride(Exception):
        pass

    with patch("voiceai.llms.litellm.acompletion", fake_acompletion):
        with pytest.raises(_SentinelOverride):
            async for _ in llm.generate_stream([{"role": "user", "content": "hi"}], tools=[_server_id_tool("b")]):
                pass
    assert _tool_names(captured["tools"]) == ["b"]
    assert "call_sid" not in captured["tools"][0]["function"]["parameters"]["properties"]


def test_gemini_init_strips_server_injected_params() -> None:
    """Gemini declarations built at init must not expose call_sid/stream_sid."""
    llm = GeminiLLM(
        model="gemini-2.5-flash",
        llm_key="test-key",
        run_id="run-1",
        api_tools={"tools_params": {"book": {"url": CONFIGURED_URL}}, "tools": [_server_id_tool()]},
    )
    assert llm.gemini_tools is not None
    decl = llm.gemini_tools[0].function_declarations[0]
    params = decl.parameters.model_dump() if hasattr(decl.parameters, "model_dump") else decl.parameters
    props = (params or {}).get("properties", {})
    assert "call_sid" not in props and "stream_sid" not in props
    assert "customer_id" in props


def test_gemini_build_config_respects_per_node_tools_override() -> None:
    """A per-node tools= subset must reach the Gemini request, stripped."""
    llm = GeminiLLM(
        model="gemini-2.5-flash",
        llm_key="test-key",
        run_id="run-1",
        api_tools={
            "tools_params": {"a": {"url": CONFIGURED_URL}, "b": {"url": CONFIGURED_URL}},
            "tools": [_server_id_tool("a"), _server_id_tool("b")],
        },
    )
    config = llm._build_config("sys", tools=[_server_id_tool("b")])
    assert config.tools is not None
    assert [d.name for d in config.tools[0].function_declarations] == ["b"]
    decl = config.tools[0].function_declarations[0]
    params = decl.parameters.model_dump() if hasattr(decl.parameters, "model_dump") else decl.parameters
    assert "call_sid" not in (params or {}).get("properties", {})


def test_gemini_build_config_empty_tools_omits_tools() -> None:
    """An empty per-call tools list (all tools scoped away) must omit tools entirely."""
    llm = GeminiLLM(
        model="gemini-2.5-flash",
        llm_key="test-key",
        run_id="run-1",
        api_tools={
            "tools_params": {"a": {"url": CONFIGURED_URL}},
            "tools": [_server_id_tool("a")],
        },
    )
    config = llm._build_config("sys", tools=[])
    assert config.tools is None


def test_gemini_build_config_forced_tool_choice_sets_tool_config() -> None:
    """A forced tool_choice must pin the Gemini request to that function (ANY mode)."""
    from google.genai.types import FunctionCallingConfigMode

    llm = GeminiLLM(
        model="gemini-2.5-flash",
        llm_key="test-key",
        run_id="run-1",
        api_tools={
            "tools_params": {"book": {"url": CONFIGURED_URL}},
            "tools": [_server_id_tool("book")],
        },
    )
    config = llm._build_config(
        "sys",
        tools=[_server_id_tool("book")],
        tool_choice={"type": "function", "function": {"name": "book"}},
    )
    assert config.tool_config is not None
    fcc = config.tool_config.function_calling_config
    assert fcc is not None
    assert fcc.mode == FunctionCallingConfigMode.ANY
    assert list(fcc.allowed_function_names or []) == ["book"]


def test_gemini_build_config_no_force_leaves_tool_config_unset() -> None:
    """Without a forced tool_choice the Gemini request must not pin a function."""
    llm = GeminiLLM(
        model="gemini-2.5-flash",
        llm_key="test-key",
        run_id="run-1",
        api_tools={
            "tools_params": {"book": {"url": CONFIGURED_URL}},
            "tools": [_server_id_tool("book")],
        },
    )
    assert llm._build_config("sys").tool_config is None
