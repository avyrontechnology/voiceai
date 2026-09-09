"""Silent drops in _synthesize must not wedge response_in_pipeline.

Two paths dropped audio without clearing pipeline flags, so every later
silence-recovery branch stayed gated off and the call went mute:
(a) invalid-sequence drop, (b) synthesizer exception. Both must log and
clear response_in_pipeline/_synthesis_awaiting_first_audio.
"""

from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager


def _synth_stub(*, valid_sequence: bool = True, push_error: Exception | None = None):
    stub = MagicMock()
    stub.conversation_ended = False
    stub.response_in_pipeline = True
    stub._synthesis_awaiting_first_audio = True
    stub._turn_audio_flushed = MagicMock()
    stub.synthesizer_provider = "sarvam"
    stub.task_config = {"tools_config": {"output": {"provider": "x"}}}
    stub.run_id = "r"
    stub.synthesizer_characters = 0
    stub.conversation_start_init_ts = 0.0
    # is_valid_sequence gate:
    stub.interruption_manager = MagicMock()
    stub.interruption_manager.is_valid_sequence.return_value = valid_sequence
    # tools:
    synth_tool = MagicMock()
    if push_error is not None:
        synth_tool.push = AsyncMock(side_effect=push_error)
    else:
        synth_tool.push = AsyncMock()
    synth_tool.get_engine.return_value = "bulbul:v2"
    stub.tools = {"synthesizer": synth_tool}
    # Bind real method:
    stub._synthesize = TaskManager._synthesize.__get__(stub, TaskManager)
    return stub, synth_tool


async def test_invalid_sequence_drop_clears_pipeline_flags():
    stub, _ = _synth_stub(valid_sequence=False)
    msg = {"data": "hello", "meta_info": {"sequence_id": 9, "is_md5_hash": False, "is_first_message": False}}
    await stub._synthesize(msg)
    assert stub.response_in_pipeline is False
    assert stub._synthesis_awaiting_first_audio is False


async def test_synthesizer_exception_clears_pipeline_flags():
    stub, _ = _synth_stub(valid_sequence=True, push_error=RuntimeError("tts boom"))
    msg = {"data": "hello", "meta_info": {"sequence_id": 9, "is_md5_hash": False, "is_first_message": False}}
    # SUPPORTED check: force the push branch by patching the constant lookup.
    import voiceai.agent_manager.task_manager as tm

    orig = tm.SUPPORTED_SYNTHESIZER_MODELS
    try:
        tm.SUPPORTED_SYNTHESIZER_MODELS = {stub.synthesizer_provider: True}
        await stub._synthesize(msg)
    finally:
        tm.SUPPORTED_SYNTHESIZER_MODELS = orig
    assert stub.response_in_pipeline is False
    assert stub._synthesis_awaiting_first_audio is False
    stub._turn_audio_flushed.set.assert_called()
