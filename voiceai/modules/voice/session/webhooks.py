"""Call webhooks + api-call ledger: verbatim move of TaskManager lines ~309-543 (spec 0027).

The pre-call webhook (fire-and-forget POST before a tool's main request, with
dispatch-URL vs direct fallback), the call-context builder, the header
sanitizer, the LLM latency stamper, the runtime-args extractor, and the
api-call detail start/finalize ledger — moved byte-identically, quirks
included (background-task retention, swallowed webhook errors, `<redacted>`
header set, naive `datetime.now()` stamps).

`WebhookSession` is the typed facade of exactly what these bodies touch;
`task_manager.py` keeps same-named thin delegators. Logger channel moves to
`otobaai.voice` per the move discipline (RUNBOOK logging continuity covers
the mapping); every message string is verbatim.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from datetime import datetime
from typing import Any, Protocol

import aiohttp

from voiceai.common.logger import get_logger
from voiceai.enums import LogComponent, LogDirection
from voiceai.modules.voice.adapters.function_runtime import (
    convert_to_request_log,
    prepare_api_request,
    validate_outbound_url,
)
from voiceai.modules.voice.constants import MODULE_NAME

__all__ = [
    "WebhookSession",
    "build_call_context",
    "extract_api_call_runtime_args",
    "finalize_api_call_detail",
    "fire_pre_call_webhook",
    "sanitize_api_call_headers",
    "stamp_llm_latency_dict",
    "start_api_call_detail",
]

logger = get_logger(MODULE_NAME)


class WebhookSession(Protocol):
    """The session surface the webhook bodies touch (duck-typed at runtime)."""

    run_id: Any
    assistant_id: Any
    tools: Any
    context_data: Any
    llm_config: Any
    conversation_start_init_ts: Any
    function_tool_api_call_details: Any
    background_tasks: Any


def sanitize_api_call_headers(headers: Any) -> Any:
    """Redact credential headers, passing anything else through untouched."""
    if not isinstance(headers, dict):
        return headers

    redacted_headers = {}
    sensitive_keys = {"authorization", "proxy-authorization", "x-api-key", "api-key"}
    for key, value in headers.items():
        if str(key).lower() in sensitive_keys:
            redacted_headers[key] = "<redacted>"
        else:
            redacted_headers[key] = value
    return redacted_headers


def stamp_llm_latency_dict(
    session: WebhookSession,
    latency_dict: dict,
    meta_info: dict,
    actual_input_tokens: Any,
    actual_output_tokens: Any,
    actual_reasoning_tokens: Any,
    actual_cached_tokens: Any,
    response_text: str | None = None,
) -> None:
    """Stamp observability fields onto an LLM turn latency dict.

    Called from both the function-call and regular-text branches of
    __do_llm_generation so the two paths stay in sync automatically.
    """
    latency_dict["turn_id"] = meta_info.get("turn_id")
    latency_dict["llm_start_ms"] = (
        round(meta_info.get("llm_start_time", 0) * 1000 - session.conversation_start_init_ts, 2)
        if meta_info.get("llm_start_time")
        else None
    )
    _t = session.tools.get("transcriber")
    if hasattr(_t, "transcribers") and hasattr(_t, "active_label"):
        _t = _t.transcribers.get(_t.active_label, _t)
    latency_dict["asr_turn_id"] = getattr(_t, "turn_counter", None)
    latency_dict["model"] = session.llm_config.get("model") if session.llm_config else None
    latency_dict["input_tokens"] = actual_input_tokens
    latency_dict["output_tokens"] = actual_output_tokens
    latency_dict["reasoning_tokens"] = actual_reasoning_tokens
    latency_dict["cached_tokens"] = actual_cached_tokens
    if response_text:
        latency_dict["response_text"] = response_text.strip()


def extract_api_call_runtime_args(resp: Any) -> Any:
    """Drop response payloads from runtime args before ledgering."""
    excluded_keys = {"model_response", "textual_response"}
    return {key: copy.deepcopy(value) for key, value in resp.items() if key not in excluded_keys}


def build_call_context(session: WebhookSession) -> Any:
    """Common call-state fields included in the pre-call webhook payload.

    These mirror the identifiers carried by the platform's customer-facing
    call-state event webhooks (execution_id/agent_id/provider/numbers) — NOT the
    transfer-specific or internal fields (call_sid/stream_sid are excluded there).
    """
    recipient_data = (session.context_data or {}).get("recipient_data") or {}
    return {
        "execution_id": session.run_id,
        "agent_id": session.assistant_id,
        "provider": session.tools["input"].io_provider,
        "from_number": recipient_data.get("from_number"),
        "to_number": recipient_data.get("to_number"),
    }


def fire_pre_call_webhook(
    session: WebhookSession, webhook_url: Any, called_fun: Any, resp: Any, meta_info: Any, webhook_param: Any = None
) -> None:
    """Fire-and-forget pre-call webhook before the tool's main request runs.

    ``params`` = the ``pre_call_webhook_param`` template substituted with the LLM args
    (else empty). Two delivery modes:
      * If ``PRE_CALL_WEBHOOK_DISPATCH_URL`` is set, POST {execution_id, webhook_url,
        params} to it; the backend enriches with the full execution record + params and
        forwards to the customer's webhook_url.
      * Otherwise (fallback), POST directly to the customer's webhook_url with
        params + the common call-state fields.
    Never blocks or fails the main tool call: background task, errors swallowed.
    """
    excluded = {"model_response", "textual_response", "tool_call_id", "resp"}
    llm_args = {key: copy.deepcopy(value) for key, value in resp.items() if key not in excluded}

    # Default missing %(name)s placeholders to "" so one absent field doesn't crash the
    # whole substitution and wipe the payload.
    params = {}
    if webhook_param:
        template_str = webhook_param if isinstance(webhook_param, str) else json.dumps(webhook_param)
        substitution_args = {name: "" for name in re.findall(r"%\((\w+)\)s", template_str)}
        substitution_args.update(llm_args)
        try:
            prepared = prepare_api_request(webhook_param, None, None, **substitution_args)
            if prepared.get("api_params") is not None:
                params = prepared["api_params"]
        except Exception as exc:
            logger.warning(f"pre_call_webhook_param substitution failed: {exc}")

    dispatch_url = os.getenv("PRE_CALL_WEBHOOK_DISPATCH_URL")
    if dispatch_url:
        # Backend dispatch: it fetches the execution record and merges params.
        target_url = dispatch_url
        payload = {"execution_id": session.run_id, "webhook_url": webhook_url, "params": params}
    else:
        # Fallback: post directly to the customer URL with params + common call-state fields.
        target_url = webhook_url
        payload = {**params, **build_call_context(session)}

    # Record in function_tool_api_call_details so the pre-call webhook lands in the
    # same per-call S3 record as the other API/tool calls.
    api_call_detail = start_api_call_detail(
        session,
        called_fun=f"{called_fun}:pre_call_webhook",
        url=target_url,
        method="POST",
        param=None,
        headers={"Content-Type": "application/json"},
        meta_info=meta_info,
        runtime_args={"tool_call_id": resp.get("tool_call_id", "")},
        request_body=json.dumps(payload),
        api_params=payload,
    )

    async def send() -> None:
        try:
            # ``target_url`` is user-controlled only on the direct fallback path;
            # the dispatch URL is operator-set env config and may be internal.
            if not dispatch_url:
                await validate_outbound_url(target_url)
            convert_to_request_log(
                str(payload),
                meta_info,
                None,
                LogComponent.FUNCTION_CALL,
                direction=LogDirection.REQUEST,
                run_id=session.run_id,
            )
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http_session:
                # allow_redirects=False: a redirect hop is not re-validated and would
                # reopen the SSRF path past the pre-flight check above.
                async with http_session.post(target_url, json=payload, allow_redirects=False) as response:
                    response_text = await response.text()
                    logger.info(f"pre_call_webhook response ({response.status}): {response_text}")
                    convert_to_request_log(
                        str(response_text),
                        meta_info,
                        None,
                        LogComponent.FUNCTION_CALL,
                        direction=LogDirection.RESPONSE,
                        run_id=session.run_id,
                    )
                    finalize_api_call_detail(
                        api_call_detail,
                        response=response_text,
                        status_code=response.status,
                        content_type=response.headers.get("Content-Type"),
                    )
        except Exception as exc:
            logger.warning(f"pre_call_webhook to {target_url} failed (ignored): {exc}")
            finalize_api_call_detail(api_call_detail, error=exc)

    # Keep a strong reference so the task isn't garbage-collected before the POST
    # finishes (the loop only holds a weak ref); drop it once done. Lazy-init the set
    # so this never depends on __init__ (robust to merge churn).
    if not hasattr(session, "background_tasks"):
        session.background_tasks = set()
    task = asyncio.create_task(send())
    session.background_tasks.add(task)
    task.add_done_callback(session.background_tasks.discard)


def start_api_call_detail(
    session: WebhookSession,
    *,
    called_fun: Any,
    url: Any,
    method: Any,
    param: Any,
    headers: Any,
    meta_info: Any,
    runtime_args: Any,
    request_body: Any = None,
    api_params: Any = None,
) -> Any:
    """Open a pending api-call ledger row on the session."""
    api_call_detail = {
        "tool_name": called_fun,
        "tool_call_id": runtime_args.get("tool_call_id", ""),
        "url": url,
        "method": method.upper() if isinstance(method, str) else method,
        "request_template": copy.deepcopy(param),
        "request_body": copy.deepcopy(request_body),
        "request_params": copy.deepcopy(api_params if api_params is not None else runtime_args),
        "runtime_args": copy.deepcopy(runtime_args),
        "headers": sanitize_api_call_headers(copy.deepcopy(headers)),
        "meta": {
            "request_id": meta_info.get("request_id"),
            "sequence_id": meta_info.get("sequence_id"),
            "turn_id": meta_info.get("turn_id"),
        },
        "started_at": datetime.now().isoformat(),
        "status": "pending",
        "response_status_code": None,
        "response_content_type": None,
        "response_body": None,
        "response_json": None,
    }
    session.function_tool_api_call_details.append(api_call_detail)
    return api_call_detail


def finalize_api_call_detail(
    api_call_detail: Any, response: Any = None, status_code: Any = None, content_type: Any = None, error: Any = None
) -> None:
    """Close a ledger row: latency, status, and a best-effort JSON parse."""
    if api_call_detail is None:
        return

    completed_at = datetime.now()
    api_call_detail["completed_at"] = completed_at.isoformat()
    api_call_detail["latency_ms"] = None
    started_at = api_call_detail.get("started_at")
    if started_at:
        try:
            started_at_dt = datetime.fromisoformat(started_at)
            api_call_detail["latency_ms"] = round((completed_at - started_at_dt).total_seconds() * 1000, 2)
        except ValueError:
            logger.warning(f"Could not compute api call latency from started_at={started_at}")
    if error is not None:
        api_call_detail["status"] = "error"
        api_call_detail["error"] = str(error)
    else:
        api_call_detail["status"] = "completed"
    api_call_detail["response_status_code"] = status_code
    api_call_detail["response_content_type"] = content_type
    api_call_detail["response_body"] = copy.deepcopy(response)
    try:
        api_call_detail["response_json"] = (
            json.loads(response) if isinstance(response, str) else copy.deepcopy(response)
        )
    except (TypeError, json.JSONDecodeError):
        api_call_detail["response_json"] = None
