import json
import logging
import re
from typing import Any, List, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict

from voiceai.enums import ToolScope

_logger = logging.getLogger(__name__)


class APIParams(BaseModel):
    url: Optional[str] = None
    method: Optional[str] = "POST"
    api_token: Optional[str] = None
    param: Optional[Union[str, dict]] = None
    headers: Optional[Union[str, dict]] = None
    pre_call_message: Optional[Union[str, dict]] = None
    pre_call_webhook_url: Optional[str] = None
    pre_call_webhook_param: Optional[Union[str, dict]] = None
    # Graph-agent tool scope; None == GLOBAL. NODE limits visibility to ``nodes``.
    scope: Optional[ToolScope] = None
    nodes: Optional[List[str]] = None


class LatencyData(BaseModel):
    sequence_id: Optional[int] = None
    first_token_latency_ms: Optional[float] = None
    total_stream_duration_ms: Optional[float] = None
    connection_latency_ms: Optional[float] = None
    service_tier: Optional[str] = None
    llm_host: Optional[str] = None
    reasoning_effort: Optional[str] = None
    verbosity: Optional[str] = None


class FunctionCallPayload(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())

    url: Optional[str] = None
    method: Optional[str] = None
    param: Any = None
    api_token: Optional[str] = None
    headers: Optional[dict] = None
    model_args: dict = {}
    meta_info: dict = {}
    called_fun: str = ""
    model_response: list[dict] = []
    tool_call_id: str = ""
    textual_response: Optional[str] = None
    resp: Any = None


# Named parameters of the two functions this payload is splatted into — TaskManager's
# ``__execute_function_call(..., next_step, called_fun, **resp)`` and ``trigger_api(url, method,
# param, api_token, headers_data, meta_info, run_id, return_response_metadata, **kwargs)`` — that are
# not FunctionCallPayload fields. A model-emitted argument by one of these names survives in ``resp``
# and makes the splat raise "got multiple values for keyword argument", killing the tool call.
_ENGINE_OWNED_TOOL_ARGUMENT_KEYS = frozenset(
    {"self", "next_step", "headers_data", "run_id", "return_response_metadata"}
)

# Every declared FunctionCallPayload field: url, method, param, api_token, headers, model_args,
# meta_info, called_fun, model_response, tool_call_id, textual_response, resp — plus the engine-owned
# names above. They are set from the agent's tool configuration (or owned by the engine), so a
# model-emitted tool argument may never overwrite one: a caller who steers the model into emitting
# ``url`` or ``api_token`` would otherwise redirect and re-authenticate the outbound API call.
RESERVED_TOOL_ARGUMENT_KEYS = frozenset(FunctionCallPayload.model_fields) | _ENGINE_OWNED_TOOL_ARGUMENT_KEYS


def apply_tool_arguments(
    payload: FunctionCallPayload, parsed_args: Any, *, logger: Optional[logging.Logger] = None
) -> dict:
    """Copy model-emitted tool arguments onto ``payload`` as extra attributes; return what was kept.

    Dropped, with one WARNING per call naming them: keys in :data:`RESERVED_TOOL_ARGUMENT_KEYS`,
    non-string keys, private names, and names that would shadow a class attribute (``model_dump``,
    ``copy``...) — pydantic sets those on the instance instead of in the extras, which breaks every
    later ``payload.model_dump()``. ``payload.resp`` is left untouched. The returned dict is exactly
    what landed in the extras, so ``model_dump()`` — the ``**resp`` the task manager forwards to
    ``trigger_api`` — can never carry a reserved key from the model.
    """
    log = logger or _logger
    if not isinstance(parsed_args, Mapping):
        log.warning(f"Tool call {payload.called_fun!r}: arguments are not a JSON object, ignoring them")
        return {}

    accepted: dict = {}
    rejected: list = []
    for key, value in parsed_args.items():
        protected = (
            not isinstance(key, str)
            or key in RESERVED_TOOL_ARGUMENT_KEYS
            or key.startswith("_")
            or hasattr(type(payload), key)
        )
        if protected:
            rejected.append(str(key))
            continue
        try:
            setattr(payload, key, value)
        except (AttributeError, TypeError, ValueError) as exc:
            log.warning(f"Tool call {payload.called_fun!r}: could not apply argument {key!r}: {exc}")
            continue
        accepted[key] = value

    if rejected:
        log.warning(
            f"Tool call {payload.called_fun!r}: ignoring model-emitted argument(s) that collide with "
            f"reserved or engine-owned payload fields: {sorted(rejected)}"
        )
    return accepted


REDACTED_VALUE = "***"
# A key is secret when one of its words (split on non-alphanumerics) is one of these...
_SECRET_KEY_WORDS = frozenset(
    {"apikey", "token", "secret", "password", "passwd", "authorization", "auth", "credential", "credentials", "bearer"}
)
# ...or it contains one of these fragments ("x-api-key", "apiKey", "OPENAI_API_TOKEN").
_SECRET_KEY_FRAGMENTS = ("api_key", "api-key", "apikey", "api_token", "api-token")
# Header bags may arrive as JSON strings; one that does not parse is redacted whole.
_HEADER_KEYS = frozenset({"headers", "headers_data", "header"})
_MAX_REDACT_DEPTH = 12


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
        return True
    return any(word in _SECRET_KEY_WORDS for word in re.split(r"[^a-z0-9]+", lowered))


def _redact_header_string(raw: str, depth: int) -> Any:
    if not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return REDACTED_VALUE
    return redact_secrets(parsed, _depth=depth + 1) if isinstance(parsed, Mapping) else REDACTED_VALUE


def redact_secrets(value: Any, *, _depth: int = 0) -> Any:
    """A copy of ``value`` that is safe to log: values under secret-looking keys become ``"***"``.

    Walks dicts, lists, tuples and pydantic models. Header bags given as JSON strings are parsed and
    walked; one that cannot be parsed is redacted whole. Everything else is returned unchanged.
    """
    if _depth > _MAX_REDACT_DEPTH:
        return REDACTED_VALUE
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if _is_secret_key(key):
                out[key] = REDACTED_VALUE if item not in (None, "") else item
            elif isinstance(key, str) and key.lower() in _HEADER_KEYS and isinstance(item, str):
                out[key] = _redact_header_string(item, _depth)
            else:
                out[key] = redact_secrets(item, _depth=_depth + 1)
        return out
    if isinstance(value, list):
        return [redact_secrets(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item, _depth=_depth + 1) for item in value)
    return value


class LLMStreamChunk(BaseModel):
    """Single chunk yielded from LLM generate_stream methods."""

    data: Any = None
    end_of_stream: bool = False
    latency: Optional[LatencyData] = None
    is_function_call: bool = False
    function_name: Optional[str] = None
    function_message: Optional[Union[str, dict]] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    overflowed: bool = False
    reasoning_content: Optional[str] = None
