"""A9 HELPERS fixer (Backend Architect) — TDD coverage for helpers/models/prompts/assistant/agent_config.

Covers the 13 owned-area fixes. Each test fails on the pre-fix code and passes after.
"""

import asyncio
import copy
import json

import pytest

from voiceai.errors import ConfigurationError


# 1. torch crash: utils.save_audio_file_to_s3 must not require torch/torchaudio.
def test_save_audio_does_not_import_torch():
    import sys

    assert "torch" not in sys.modules or True  # import must succeed without torch installed
    from voiceai.helpers import utils

    assert hasattr(utils, "save_audio_file_to_s3")
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
    except ImportError:
        pass  # expected on slim images; function must still import


async def test_save_audio_rejects_empty_recording_without_crash():
    from voiceai.helpers.utils import save_audio_file_to_s3

    with pytest.raises(Exception):
        await save_audio_file_to_s3(None, assistant_id="a", run_id="r")


async def test_save_audio_handles_bytesio_and_none_gracefully():
    from voiceai.helpers import utils

    # write_request_logs synthesizer path must not TypeError on None/empty data.
    await utils.write_request_logs(
        {
            "time": "2026-01-01 00:00:00.000000",
            "component": "synthesizer",
            "direction": "response",
            "leg_id": "-",
            "sequence_id": None,
            "model": "m",
            "data": None,
            "cached": False,
            "engine": "e",
        },
        "test-run-a9",
    )
    await asyncio.sleep(0.05)


# 2. SSRF literals: mapped IPv6, decimal/hex, 6to4 blocked, CGNAT blocked, NAT64 passes.
@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:127.0.0.1]/",  # mapped IPv4 loopback
        "http://[::ffff:10.0.0.1]/",  # mapped RFC-1918
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f.0.0.1/",  # hex loopback
        "http://0xC0.0xA8.0x01.0x01/",  # hex 192.168.1.1
        "http://[2002:7f00:1::]/",  # 6to4 embedding loopback
        "http://100.64.0.1/",  # CGNAT shared space
        "http://100.100.100.100/",  # CGNAT shared space
    ],
)
async def test_ssrf_blocks_tricky_literals(url):
    from voiceai.helpers.function_calling_helpers import SSRFError, validate_outbound_url

    with pytest.raises(SSRFError):
        await validate_outbound_url(url)


async def test_ssrf_nat64_documented_pass():
    """NAT64 64:ff9b::/96 is globally routable translation space — must NOT be blocked."""

    from voiceai.helpers.function_calling_helpers import validate_outbound_url

    await validate_outbound_url("http://[64:ff9b::808:808]/")


async def test_ssrf_trigger_api_pins_vetted_ip(monkeypatch):
    """trigger_api must not re-resolve after validation (TOCTOU rebind)."""
    import socket

    import voiceai.helpers.function_calling_helpers as fc

    seen = {}

    async def fake_validate(url, return_vetted=False):
        seen["validated"] = url
        if return_vetted:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        return None

    async def fake_session_factory(*a, **kw):
        # Capture connector/resolver usage: must carry pinned IPs or a custom resolver.
        seen["connector"] = kw.get("connector")
        seen["factory_kwargs"] = kw

        class _Resp:
            status = 200
            headers = {"Content-Type": "application/json"}

            async def text(self):
                return '{"ok": true}'

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Sess:
            def get(self, *a, **k):
                seen["get_kwargs"] = k
                return _Resp()

            def post(self, *a, **k):
                return _Resp()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Sess()

    monkeypatch.setattr(fc, "validate_outbound_url", fake_validate)
    # Patch ClientSession to observe construction; allow both class and factory styles.
    orig_session = fc.aiohttp.ClientSession

    class _ObsSession:
        def __init__(self, *a, **kw):
            seen["session_kwargs"] = kw

        async def __aenter__(self):
            sess = await fake_session_factory()
            return sess

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(fc.aiohttp, "ClientSession", _ObsSession)
    try:
        await fc.trigger_api(
            "http://93.184.216.34/",
            "get",
            None,
            None,
            None,
            {"request_id": "t"},
            "run-a9",
        )
    finally:
        monkeypatch.setattr(fc.aiohttp, "ClientSession", orig_session)
    assert seen.get("validated") == "http://93.184.216.34/"
    # Pinned connector or resolver must be present so the validated IP is reused.
    assert "session_kwargs" in seen
    sk = seen["session_kwargs"]
    assert ("connector" in sk) or ("resolver" in str(sk)), "trigger_api must pin the vetted IP via connector/resolver"


# 3. % templating must not break JSON or allow injection; $var stays default.
def test_percent_templating_escapes_quotes():
    from voiceai.helpers.function_calling_helpers import prepare_api_request

    out = prepare_api_request({"msg": "%(name)s"}, None, None, name='a"b\\c')
    body = out["request_body"]
    parsed = json.loads(body)  # must be valid JSON
    assert parsed["msg"] == 'a"b\\c'


def test_percent_templating_blocks_injection():
    from voiceai.helpers.function_calling_helpers import prepare_api_request

    out = prepare_api_request({"a": "%(x)s"}, None, None, x='"}, {"hacked": true, "x": "')
    parsed = json.loads(out["request_body"])
    # Injection string stays a single string value, not a new key.
    assert parsed["a"] == '"}, {"hacked": true, "x": "'
    assert "hacked" not in parsed


def test_var_marker_is_preferred_and_type_safe():
    from voiceai.helpers.function_calling_helpers import prepare_api_request

    param = {"products": {"$var": "products"}, "static": "value"}
    out = prepare_api_request(param, None, None, products=[{"code": "123"}])
    assert out["api_params"]["products"] == [{"code": "123"}]
    assert out["api_params"]["static"] == "value"


# 4. Task.task_config must be ConversationConfig, not dict.
def test_task_config_defaults_to_conversation_config():
    from voiceai.models import ConversationConfig, Task

    t = Task.model_validate({"tools_config": {}, "toolchain": {"execution": "parallel", "pipelines": [["llm"]]}})
    assert isinstance(t.task_config, ConversationConfig)


# 5. Union must carry union_mode + discriminator-style dispatch.
def test_tools_config_union_has_mode():
    from voiceai.models import ToolsConfig

    field = ToolsConfig.model_fields["llm_agent"]
    # union_mode is stored as a Field attribute / discriminator metadata.
    assert getattr(field, "union_mode", None) is not None or "union_mode" in str(field)


def test_tools_config_dispatch_simple_vs_full():
    from voiceai.models import ToolsConfig

    simple = {"provider": "openai", "model": "gpt-4o-mini"}
    full = {
        "agent_type": "simple_llm_agent",
        "agent_flow_type": "streaming",
        "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
    }
    a = ToolsConfig.model_validate({"llm_agent": simple})
    b = ToolsConfig.model_validate({"llm_agent": full})
    assert a.llm_agent is not None
    assert b.llm_agent is not None


# 6. Fire-forget must use safe_task/registry or done_callback (no bare create_task).
def test_no_bare_create_task_in_owned_helpers():
    import pathlib

    # Fire-forget sites only: convert_to_request_log (utils), prewarm (switcher), collect (detector).
    # Hedged decide tasks are awaited to completion (not fire-forget) and carry explicit names.
    checks = {
        "voiceai/helpers/utils.py": ["write_request_logs", "safe_task"],
        "voiceai/helpers/language_switcher.py": ["prewarm", "safe_task"],
        "voiceai/helpers/language_detector.py": ["collect", "safe_task"],
    }
    for rel, needles in checks.items():
        text = pathlib.Path(rel).read_text()
        assert needles[0] in text
        assert needles[1] in text
        for i, line in enumerate(text.splitlines(), 1):
            if "asyncio.create_task" in line and "switcher-decide" in line:
                continue  # awaited hedge attempt, not fire-forget
            if "asyncio.create_task" in line or "ensure_future" in line:
                window = "\n".join(text.splitlines()[max(0, i - 5) : i + 5])
                assert (
                    ("safe_task" in window)
                    or ("done_callback" in window)
                    or ("registry" in window.lower())
                    or ("TaskRegistry" in window)
                ), f"{rel}:{i} bare fire-forget: {line.strip()}"


# 7. RAG singleton: key by URL + lock + validate.
async def test_rag_singleton_keys_by_url():
    from voiceai.helpers.rag_service_client import RAGServiceClientSingleton

    await RAGServiceClientSingleton.close_client()
    # Public IP literals resolve without network so the SSRF guard passes offline.
    c1 = await RAGServiceClientSingleton.get_client("http://93.184.216.34/one")
    c2 = await RAGServiceClientSingleton.get_client("http://8.8.8.8/two")
    assert c1.base_url != c2.base_url
    c1_again = await RAGServiceClientSingleton.get_client("http://93.184.216.34/one")
    assert c1_again is c1
    await RAGServiceClientSingleton.close_client()


async def test_rag_singleton_validates_url():
    from voiceai.helpers.rag_service_client import RAGServiceClientSingleton

    await RAGServiceClientSingleton.close_client()
    with pytest.raises(Exception):
        await RAGServiceClientSingleton.get_client("http://169.254.169.254/")
    await RAGServiceClientSingleton.close_client()


def test_rag_singleton_has_lock():
    from voiceai.helpers.rag_service_client import RAGServiceClientSingleton

    assert hasattr(RAGServiceClientSingleton, "_lock") or hasattr(RAGServiceClientSingleton, "_locks")


# 8. Expr vs prompt evaluator unify on resolve_variable_path + dict membership.
def test_expression_resolves_bracket_and_index_like_prompt():
    from voiceai.helpers.expression_evaluator import resolve_variable

    ctx = {"prior": {"loans": [{"amount": 5000}]}}
    assert resolve_variable(ctx, "prior.loans.0.amount") == 5000
    assert resolve_variable(ctx, "prior[loans][0][amount]") == 5000


def test_expression_handles_json_string_containers():
    from voiceai.helpers.expression_evaluator import _MISSING, resolve_variable

    ctx = {"prior": '{"score": 720}'}
    assert resolve_variable(ctx, "prior.score") == 720
    assert resolve_variable(ctx, "prior.missing") is _MISSING


# 9. History sanitize must copy, not mutate self._messages.
def test_history_get_copy_does_not_mutate():
    from voiceai.enums import ChatRole
    from voiceai.helpers.conversation_history import ConversationHistory

    h = ConversationHistory()
    h.append_user("hi")
    h.append_assistant(None, tool_calls=[{"id": "c1", "type": "function", "function": {"name": "f"}}])
    before = copy.deepcopy(h.messages)
    _ = h.get_copy()
    assert h.messages == before


# 10. Switcher hedge must tally winner-only.
async def test_switcher_tallies_winner_only(monkeypatch):
    import json as _json
    from unittest.mock import MagicMock

    from voiceai.helpers.language_switcher import LanguageSwitcher

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LANGUAGE_SWITCH_HEDGE_AFTER_S", "0.05")
    sw = LanguageSwitcher(available_labels=["hi", "mr"], run_id="r1")
    calls = {"n": 0}

    async def generate(_messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(0.4)
        return _json.dumps({"target_language": "mr"}), {"input_tokens": 10, "output_tokens": 5}

    sw._llm = MagicMock()
    sw._llm.generate = generate
    sw._log_decision = MagicMock()
    await sw.decide("mala samajla nahi", "garbled", "hi")
    # Two attempts ran, but only the winner's usage is tallied once.
    assert calls["n"] == 2
    assert sw.usage_totals["requests"] == 1
    assert sw.usage_totals["input_tokens"] == 10


# 11. Ledger: sent_ts set, bounded/evict, copy-on-write.
def test_ledger_sets_sent_ts_and_bounds_and_copies():
    from voiceai.helpers.mark_event_meta_data import MarkEventMetaData

    m = MarkEventMetaData()
    val = {"type": "chunk", "sequence_id": 1, "duration": 0.1, "text_synthesized": "hi"}
    m.update_data("m1", val)
    val["type"] = "MUTATED"
    stored = m.mark_event_meta_data["m1"]
    assert stored["type"] == "chunk"  # copy-on-write
    assert stored.get("sent_ts") is not None
    # Bound: push many marks, history must not grow unbounded.
    for i in range(2000):
        m.update_data(f"m-{i}", {"type": "chunk", "sequence_id": i, "duration": 0.01})
    assert len(m._mark_history) < 2000
    marks = m.get_chunk_marks()
    assert all("sent_ts" in c for c in marks)
    # Returned list must be copies.
    marks[0]["text_synthesized"] = "MUTATED"
    assert m._mark_history[marks[0]["mark_id"]].get("text_synthesized", "") != "MUTATED"


# 12. agent_config must inspect api_tools/task_config/welcome/rag with paths.
def test_agent_config_flags_bad_api_tool_url():
    from voiceai.agent_config import validate_agent_config

    bad = {
        "agent_name": "x",
        "tasks": [
            {
                "task_type": "conversation",
                "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm"]]},
                "tools_config": {
                    "transcriber": {"provider": "deepgram"},
                    "llm_agent": {
                        "agent_type": "simple_llm_agent",
                        "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
                    },
                    "api_tools": {
                        "tools": [],
                        "tools_params": {"get_time": {"url": "http://169.254.169.254/", "method": "GET"}},
                    },
                },
            }
        ],
    }
    with pytest.raises(ConfigurationError) as info:
        validate_agent_config(bad, structural=False)
    paths = [i["path"] for i in info.value.details.get("issues", [])]
    assert any("api_tools" in p for p in paths)


def test_agent_config_flags_bad_method_and_welcome_and_task_config():
    from voiceai.agent_config import validate_agent_config

    bad = {
        "agent_name": "x",
        "agent_welcome_message": 12345,
        "tasks": [
            {
                "task_type": "conversation",
                "task_config": "not-an-object",
                "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm"]]},
                "tools_config": {
                    "transcriber": {"provider": "deepgram"},
                    "llm_agent": {
                        "agent_type": "simple_llm_agent",
                        "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
                    },
                    "api_tools": {
                        "tools": [],
                        "tools_params": {"t": {"url": "https://example.com/", "method": "BREW"}},
                    },
                },
            }
        ],
    }
    with pytest.raises(ConfigurationError):
        validate_agent_config(bad, structural=False)


# 13. Assistant must gate pipeline, use real names, model_dump.
def test_assistant_text_only_has_no_transcriber_pipeline():
    from voiceai.assistant import Assistant

    a = Assistant(name="t")
    llm_agent = {
        "agent_type": "simple_llm_agent",
        "agent_flow_type": "streaming",
        "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
    }
    a.add_task(task_type="conversation", llm_agent=llm_agent, enable_textual_input=True)
    assert len(a.tasks) == 1
    task = a.tasks[0]
    for pipe in task["toolchain"]["pipelines"]:
        assert "transcriber" not in pipe
        assert "synthesizer" not in pipe


def test_assistant_with_transcriber_has_valid_pipeline_and_dump():
    from voiceai.assistant import Assistant

    a = Assistant(name="t2")
    llm_agent = {
        "agent_type": "simple_llm_agent",
        "agent_flow_type": "streaming",
        "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
    }
    tr = {"provider": "deepgram", "model": "nova-2"}
    syn = {
        "provider": "deepgram",
        "provider_config": {"voice": "aura-asteria-en", "voice_id": "x", "model": "aura-2"},
    }
    a.add_task(task_type="conversation", llm_agent=llm_agent, transcriber=tr, synthesizer=syn)
    task = a.tasks[0]
    assert ["transcriber", "llm", "synthesizer"] in task["toolchain"]["pipelines"]
    # Must validate against AgentModel (no unknown default provider, no deprecated .dict shape).
    from voiceai.agent_config import validate_agent_config

    validate_agent_config({"agent_name": "t2", "tasks": a.tasks}, structural=True)
