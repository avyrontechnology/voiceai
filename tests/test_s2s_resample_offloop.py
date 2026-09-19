"""Resample off the event loop: S2S encode paths must not block on scipy.

_s2s_encode_input/_s2s_encode_output run per audio chunk on the realtime hot
loop; scipy.signal.resample_poly (and pydub) are CPU-bound and would stall the
loop for ms per chunk. They must run in a worker thread with identical
semantics, emitting a debug-level per-encode-ms log the operator can watch.
"""

import asyncio
import logging
import threading

import pytest

from voiceai.helpers import utils as _utils


def _silence_pcm16(samples=160, rate_in=8000):
    return b"\x00\x00" * samples


class TestAresampleParity:
    async def test_upsample_matches_sync_resample(self):
        pcm8k = _silence_pcm16(160)
        expected = _utils.resample(pcm8k, 24000, format="pcm", original_sample_rate=8000)
        got = await _utils.aresample(pcm8k, 24000, format="pcm", original_sample_rate=8000)
        assert got == expected
        assert len(got) == len(pcm8k) * 3

    async def test_downsample_matches_sync_resample(self):
        pcm24k = _silence_pcm16(480)
        expected = _utils.resample(pcm24k, 8000, format="pcm", original_sample_rate=24000)
        got = await _utils.aresample(pcm24k, 8000, format="pcm", original_sample_rate=24000)
        assert got == expected
        assert len(got) == len(pcm24k) // 3

    async def test_passthrough_and_errors_match_sync(self):
        pcm = _silence_pcm16(160)
        assert await _utils.aresample(pcm, 8000, format="pcm", original_sample_rate=8000) == pcm
        with pytest.raises(ValueError):
            await _utils.aresample(pcm, 16000, format="pcm")

    async def test_runs_off_the_event_loop_thread(self, monkeypatch):
        caller_thread = threading.get_ident()
        seen = {}

        def spy_resample(*args, **kwargs):
            seen["thread"] = threading.get_ident()
            return b"\x00\x00" * 8

        monkeypatch.setattr(_utils, "resample", spy_resample)
        # aresample must resolve resample at call time so the worker thread is the one blocked.
        import voiceai.helpers.utils as live

        assert live.aresample.__module__ == _utils.__name__
        out = await _utils.aresample(b"\x00\x00" * 8, 8000, format="pcm", original_sample_rate=8000)
        assert out == b"\x00\x00" * 8
        assert seen["thread"] != caller_thread


class TestS2SEncodeAsync:
    def test_encode_methods_are_coroutines(self):
        from voiceai.agent_manager.task_manager import TaskManager

        assert asyncio.iscoroutinefunction(TaskManager._s2s_encode_input)
        assert asyncio.iscoroutinefunction(TaskManager._s2s_encode_output)

    async def test_encode_input_matches_sync_lengths(self):
        import sys

        sys.path.insert(0, "tests")
        from test_s2s_task_manager import make_tm

        from voiceai.helpers.utils import pcm_to_ulaw

        tm = make_tm(io_provider="talko", in_rate=24000)
        tm._s2s_encode_logged = True  # silence the once-per-call info log
        out = await tm._s2s_encode_input(pcm_to_ulaw(_silence_pcm16(160)))
        assert len(out) == 960  # 160 samples @8k -> 480 @24k -> 960 bytes

    async def test_encode_output_matches_sync_lengths(self):
        import sys

        sys.path.insert(0, "tests")
        from test_s2s_task_manager import make_tm

        tm = make_tm(io_provider="plivo", out_rate=24000)
        out = await tm._s2s_encode_output(_silence_pcm16(480))
        assert len(out) == 160  # 480 samples @24k -> 160 @8k -> 160 mu-law bytes

    async def test_per_encode_ms_debug_log(self, caplog):
        import sys

        sys.path.insert(0, "tests")
        from test_s2s_task_manager import make_tm

        tm = make_tm(io_provider="plivo", out_rate=24000)
        with caplog.at_level(logging.DEBUG, logger="voiceai.agent_manager.task_manager"):
            await tm._s2s_encode_output(_silence_pcm16(480))
        lines = [r.getMessage() for r in caplog.records if "encode" in r.getMessage() and "ms=" in r.getMessage()]
        assert len(lines) >= 1
