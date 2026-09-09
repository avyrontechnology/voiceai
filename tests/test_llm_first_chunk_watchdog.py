"""First-chunk watchdog: a hung LLM stream must not wedge response_in_pipeline.

generate_content_stream can open but yield zero chunks for seconds (measured
46s TTFT with thinking); each attempt was cancelled by the next utterance,
so the agent stayed silent forever. The watchdog bounds the wait for the
first LLM chunk (~4-5s), logs a distinct LLM_FIRST_CHUNK_TIMEOUT event,
cancels the hung stream, clears pipeline flags, and records into
meta_info _non_fatal_errors so the empty-turn tail explains the silence.
"""

import asyncio

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import LLM_FIRST_CHUNK_TIMEOUT_S


class _Stub:
    pass


def _mgr() -> _Stub:
    stub = _Stub()
    stub.response_in_pipeline = True
    stub._synthesis_awaiting_first_audio = True
    return stub


async def _slow_stream():
    await asyncio.sleep(10)
    yield {"data": "never"}


async def _fast_stream():
    yield {"data": "hello"}
    yield {"data": "world"}


async def test_watchdog_constant_in_range():
    assert 4.0 <= LLM_FIRST_CHUNK_TIMEOUT_S <= 5.0


async def test_slow_stream_times_out_clears_flags_and_records():
    stub = _mgr()
    meta: dict = {}
    out = []
    async for item in TaskManager._llm_stream_with_first_chunk_timeout(stub, _slow_stream(), meta, timeout_s=0.05):
        out.append(item)
    assert out == []
    assert stub.response_in_pipeline is False
    assert stub._synthesis_awaiting_first_audio is False
    errors = meta.get("_non_fatal_errors", [])
    assert any(e.get("error") == "LLM_FIRST_CHUNK_TIMEOUT" for e in errors)


async def test_fast_stream_passes_through_untouched():
    stub = _mgr()
    meta: dict = {}
    out = []
    async for item in TaskManager._llm_stream_with_first_chunk_timeout(stub, _fast_stream(), meta, timeout_s=2.0):
        out.append(item)
    assert [m["data"] for m in out] == ["hello", "world"]
    # No timeout: flags untouched, no error recorded.
    assert stub.response_in_pipeline is True
    assert meta.get("_non_fatal_errors", []) == []
