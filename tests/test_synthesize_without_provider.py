"""_synthesize must never crash when no synthesizer is configured.

Regression: on an S2S (or synth-less) agent, __setup_synthesizer is skipped so
`synthesizer_provider` is never set, but the browser welcome path still called
_synthesize -> AttributeError, killing the greeting with total UI silence.
A missing provider must log + clear the pipeline, never raise. S2S web legs
must skip the pre-rendered greeting entirely (the model speaks its own via
trigger_response in _run_s2s_conversation); the init ack is unaffected.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.errors import ConfigurationError


def _bare_tm(*, s2s=False, web=True):
    tm = TaskManager.__new__(TaskManager)
    tm.is_web_based_call = web
    tm.turn_based_conversation = False
    tm.conversation_ended = False
    tm.response_in_pipeline = True
    tm._synthesis_awaiting_first_audio = False
    tm.task_config = {
        "task_type": "conversation",
        "tools_config": {"output": {"provider": "default", "format": "pcm"}},
    }
    tm.s2s_config = {"provider": "gemini_live"} if s2s else None
    tm.kwargs = {"agent_welcome_message": "Hello!", "api_tools": {}, "s2s_key": "test-key"}
    tm.conversation_history = MagicMock()
    tm.stream_sid = None
    tm.stream_sid_ts = None
    tm.conversation_start_init_ts = 0
    tm.synthesizer_characters = 0
    tm.should_record = False
    tm.run_id = "test-run"
    tm._turn_audio_flushed = asyncio.Event()
    tm.interruption_manager = MagicMock()
    tm.interruption_manager.is_valid_sequence = MagicMock(return_value=True)
    tm.system_prompt = "sys"
    tm.tools = {"output": SimpleNamespace(handle=AsyncMock())}
    # Deliberately NO synthesizer_provider attr: __setup_synthesizer was skipped.
    assert not hasattr(tm, "synthesizer_provider")
    return tm


def _welcome_meta():
    return {
        "io": "default",
        "message_category": "agent_welcome_message",
        "stream_sid": None,
        "sequence_id": -1,
        "format": "pcm",
        "text": "Hello!",
        "end_of_llm_stream": True,
        "is_md5_hash": False,
    }


async def test_synthesize_without_provider_clears_pipeline_instead_of_raising():
    tm = _bare_tm()
    await tm._synthesize({"data": "Hello!", "meta_info": _welcome_meta()})
    assert tm.response_in_pipeline is False
    assert tm._synthesis_awaiting_first_audio is False


async def test_first_message_web_skips_s2s_greeting():
    tm = _bare_tm(s2s=True)
    tm._synthesize = AsyncMock()
    await tm._TaskManager__first_message()
    tm._synthesize.assert_not_awaited()


async def test_first_message_web_still_synthesizes_cascaded():
    from unittest.mock import patch

    tm = _bare_tm(s2s=False)
    tm.synthesizer_provider = "sarvam"
    tm.tools["synthesizer"] = SimpleNamespace(push=AsyncMock(), get_engine=MagicMock(return_value="e"))
    from voiceai.providers import SUPPORTED_SYNTHESIZER_MODELS

    if "sarvam" not in SUPPORTED_SYNTHESIZER_MODELS:
        pytest.skip("sarvam synthesizer not registered")
    with patch("voiceai.agent_manager.task_manager.convert_to_request_log"):
        await tm._TaskManager__first_message()
    tm.tools["synthesizer"].push.assert_awaited_once()


async def test_s2s_empty_voice_rejected_with_path():
    tm = TaskManager.__new__(TaskManager)
    tm.s2s_provider_name = "gemini_live"
    tm.s2s = SimpleNamespace(
        provider="gemini_live",
        provider_config=SimpleNamespace(model_dump=lambda **kwargs: {"model": "m", "voice": "", "language": "en"}),
    )
    tm.kwargs = {"api_tools": {}, "s2s_key": "test-key"}
    tm.system_prompt = "sys"
    with pytest.raises(ConfigurationError) as exc_info:
        tm._build_s2s_provider()
    assert "voice" in str(exc_info.value).lower()
    assert "tools_config.s2s.provider_config.voice" in getattr(exc_info.value, "path", "")


async def test_s2s_missing_voice_key_rejected_with_path():
    tm = TaskManager.__new__(TaskManager)
    tm.s2s_provider_name = "gemini_live"
    tm.s2s = SimpleNamespace(
        provider="gemini_live",
        provider_config=SimpleNamespace(model_dump=lambda **kwargs: {"model": "m", "language": "en"}),
    )
    tm.kwargs = {"api_tools": {}, "s2s_key": "test-key"}
    tm.system_prompt = "sys"
    with pytest.raises(ConfigurationError) as exc_info:
        tm._build_s2s_provider()
    assert "tools_config.s2s.provider_config.voice" in getattr(exc_info.value, "path", "")
