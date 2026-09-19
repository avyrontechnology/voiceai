"""Parallel S2S startup: connect immediately, hydrate concurrently, update post-connect.

The provider must be built and connected WITHOUT waiting for _s2s_stream_ready
(contact hydration runs concurrently in message_task_new); once the stream is
ready the re-rendered prompt is pushed through the provider's session-update
mechanism. The pre-greeting stream_ready wait stays as the only greeting gate,
and turn_based/web legs never wait.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager


def _make_tm(*, system_prompt="hello {student_name}", pre="hello {student_name}", s2s=None):
    tm = MagicMock()
    tm.system_prompt = {"role": "system", "content": system_prompt}
    tm.tools = {"s2s": s2s} if s2s is not None else {}
    tm._s2s_apply_post_connect_instructions = TaskManager._s2s_apply_post_connect_instructions.__get__(tm, TaskManager)
    return tm, pre


class TestPostConnectInstructions:
    async def test_changed_prompt_pushes_session_update(self):
        s2s = SimpleNamespace(system_prompt="old", update_instructions=AsyncMock())
        tm, pre = _make_tm(system_prompt="hello Aarav", pre="hello {student_name}", s2s=s2s)
        await tm._s2s_apply_post_connect_instructions(pre)
        s2s.update_instructions.assert_awaited_once_with("hello Aarav")

    async def test_unchanged_prompt_does_not_touch_provider(self):
        s2s = SimpleNamespace(system_prompt="same", update_instructions=AsyncMock())
        tm, pre = _make_tm(system_prompt="same", pre="same", s2s=s2s)
        await tm._s2s_apply_post_connect_instructions(pre)
        s2s.update_instructions.assert_not_awaited()

    async def test_provider_without_update_api_gets_local_refresh(self):
        # Gemini-style provider: no live-update API, so the local copy is refreshed
        # and the re-rendered greeting carries the variables instead. Never raises.
        s2s = SimpleNamespace(system_prompt="old")
        tm, pre = _make_tm(system_prompt="hello Aarav", pre="hello {student_name}", s2s=s2s)
        await tm._s2s_apply_post_connect_instructions(pre)
        assert s2s.system_prompt == "hello Aarav"

    async def test_missing_s2s_tool_is_safe(self):
        tm, pre = _make_tm(system_prompt="hello Aarav", pre="old", s2s=None)
        await tm._s2s_apply_post_connect_instructions(pre)  # must not raise

    async def test_failing_update_never_breaks_call(self):
        async def boom(_):
            raise RuntimeError("socket closed")

        s2s = SimpleNamespace(system_prompt="old", update_instructions=boom)
        tm, pre = _make_tm(system_prompt="hello Aarav", pre="old", s2s=s2s)
        await tm._s2s_apply_post_connect_instructions(pre)  # must not raise

    async def test_str_system_prompt_supported(self):
        s2s = SimpleNamespace(system_prompt="old", update_instructions=AsyncMock())
        tm = MagicMock()
        tm.system_prompt = "hello Aarav"
        tm.tools = {"s2s": s2s}
        tm._s2s_apply_post_connect_instructions = TaskManager._s2s_apply_post_connect_instructions.__get__(
            tm, TaskManager
        )
        await tm._s2s_apply_post_connect_instructions("old prompt")
        s2s.update_instructions.assert_awaited_once_with("hello Aarav")


class TestStartupOrdering:
    """Source-level guards: connect must not sit behind the stream_ready gate."""

    def test_build_and_connect_precede_every_stream_ready_wait(self):
        src = inspect.getsource(TaskManager._run_s2s_conversation)
        assert "await s2s.connect()" in src
        first_wait = src.index("_s2s_stream_ready.wait()")
        assert src.index("self._build_s2s_provider()") < first_wait
        assert src.index("await s2s.connect()") < first_wait

    def test_no_pre_build_serialization_gate(self):
        src = inspect.getsource(TaskManager._run_s2s_conversation)
        assert "stream not ready before session build" not in src

    def test_pre_greeting_gate_kept_as_the_only_greeting_gate(self):
        src = inspect.getsource(TaskManager._run_s2s_conversation)
        assert "S2S: no stream_sid before the greeting, skipping it" in src

    def test_post_connect_update_applied_on_carrier_legs(self):
        src = inspect.getsource(TaskManager._run_s2s_conversation)
        assert "_s2s_apply_post_connect_instructions" in src
        assert "carrier_leg" in src

    def test_browser_and_turn_based_legs_never_wait(self):
        src = inspect.getsource(TaskManager._run_s2s_conversation)
        # Both waits live under the carrier_leg guard; web/turn-based skip them.
        assert src.count("if carrier_leg:") >= 1
        assert "turn_based_conversation and not self.is_web_based_call" in src
