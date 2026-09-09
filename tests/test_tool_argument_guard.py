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

import pytest
from unittest.mock import MagicMock, patch

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
