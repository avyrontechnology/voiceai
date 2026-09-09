"""Same-turn cumulative ASR re-emissions must not cancel the in-flight LLM.

Sarvam re-emits the turn-so-far ("A" -> "A B") under one asr_turn_id; cancelling
the in-flight llm_task per fragment starves every attempt (each is killed by
the next fragment before first token). Only genuinely new speech (new
asr_turn_id) may cancel. The hangup-skip early return must clear pipeline
flags like the empty-turn path does, or response_in_pipeline wedges True.
"""

import asyncio
from unittest.mock import MagicMock

from voiceai.agent_manager.task_manager import TaskManager


def _kickoff_stub():
    stub = MagicMock()
    # Real attrs the method touches:
    stub.llm_task = None
    stub.interruption_manager = MagicMock()
    stub.response_in_pipeline = False
    stub._inflight_llm_asr_turn_id = None
    stub._spawn_language_switch_decision = MagicMock()
    stub._drop_all_staged_assistant_history = MagicMock()
    # Bind the REAL method under test:
    stub.kickoff_llm_generation = TaskManager.kickoff_llm_generation.__get__(stub, TaskManager)
    return stub


def _mock_inflight_task():
    t = MagicMock()
    t.done.return_value = False
    return t


async def test_same_asr_turn_id_does_not_cancel_inflight():
    import unittest.mock as mock

    stub = _kickoff_stub()
    first = _mock_inflight_task()
    stub.llm_task = first
    stub._inflight_llm_asr_turn_id = 7

    async def _fake_run(_pkg):
        return None

    stub._run_llm_task = _fake_run  # type: ignore[attr-defined]
    meta = {"sequence_id": 2, "asr_turn_id": 7}

    def _close_and_stub(coro, **_kw):
        try:
            coro.close()
        except Exception:
            pass
        return MagicMock()

    with mock.patch.object(asyncio, "create_task", side_effect=_close_and_stub):
        stub.kickoff_llm_generation("A B", meta)
    first.cancel.assert_not_called()


async def test_new_asr_turn_id_cancels_inflight():
    import unittest.mock as mock

    stub = _kickoff_stub()
    first = _mock_inflight_task()
    stub.llm_task = first
    stub._inflight_llm_asr_turn_id = 7

    async def _fake_run(_pkg):
        return None

    stub._run_llm_task = _fake_run  # type: ignore[attr-defined]
    meta = {"sequence_id": 3, "asr_turn_id": 8}

    def _close_and_stub(coro, **_kw):
        try:
            coro.close()
        except Exception:
            pass
        return MagicMock()

    with mock.patch.object(asyncio, "create_task", side_effect=_close_and_stub):
        stub.kickoff_llm_generation("new turn", meta)
    first.cancel.assert_called_once()


async def test_hangup_skip_clears_pipeline_flags():
    stub = MagicMock()
    stub.hangup_triggered = True
    stub.conversation_ended = False
    stub.response_in_pipeline = True
    stub._synthesis_awaiting_first_audio = True
    await TaskManager._TaskManager__do_llm_generation(stub, [], {"sequence_id": 1}, "synthesizer")
    assert stub.response_in_pipeline is False
    assert stub._synthesis_awaiting_first_audio is False
