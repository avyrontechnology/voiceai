"""TaskManager test double: bare_tm() with bind().

Contract (A11):
  - Always `MagicMock(spec=TaskManager)` so typos fail loudly and real
    attribute names stay in sync with the engine.
  - Never write the mangled private literal in tests. Use
    `tm.bind("__<name>")` for private (double-underscore) methods or
    `tm.bind("<name>")` for public/single-underscore methods. bind() resolves
    the mangling internally and returns the bound method.
  - bare_tm() ships sane defaults for the voice loop (task_config, tools,
    conversation_history, interruption_manager). Override via kwargs.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def _resolve_tm_attr(name: str) -> str:
    # "__foo" -> "_TaskManager__foo" (name-mangled private). Single-underscore
    # and public names pass through unchanged.
    if name.startswith("__") and not name.endswith("__"):
        return f"_TaskManager__{name[2:]}"
    return name


def stub(tm: MagicMock, name: str, value: Any) -> None:
    """Set a (possibly private) attribute without writing the mangled literal.

    Example: stub(tm, "__cleanup_downstream_tasks", AsyncMock()) instead of
    assigning the private attribute directly.
    """
    setattr(tm, _resolve_tm_attr(name), value)


def unbound_tm_attr(name: str):
    """Return the unbound TaskManager attribute (no mangled literal at call site)."""
    from voiceai.agent_manager.task_manager import TaskManager

    return getattr(TaskManager, _resolve_tm_attr(name))


def private(tm: MagicMock, name: str):
    """Read a (possibly private) instance attribute without the mangled literal.

    Use for asserting on stubbed private mocks:
    `private(tm, "__cleanup_downstream_tasks").assert_not_called()`.
    """
    return getattr(tm, _resolve_tm_attr(name))


def bare_tm(strict: bool = True, **overrides: Any) -> MagicMock:
    """Build a TaskManager double with spec + bind() helper.

    strict=True (default) uses MagicMock(spec=TaskManager) for typo safety —
    ideal for provider/config tests. strict=False uses a permissive MagicMock
    (same bind/stub API) for driving real loop methods like _listen_transcriber
    that touch dozens of dynamic attrs (voicemail_handler, guards, history).
    """
    from voiceai.agent_manager.task_manager import TaskManager

    tm = MagicMock(spec=TaskManager) if strict else MagicMock()

    def bind(name: str):
        attr = _resolve_tm_attr(name)
        return getattr(TaskManager, attr).__get__(tm, TaskManager)

    # bind() is a test-only helper, not part of the TaskManager spec.
    tm.bind = bind  # type: ignore[attr-defined]

    # --- sane voice-loop defaults (tests override as needed) ---
    tm.task_config = {
        "tools_config": {
            "transcriber": {"provider": "deepgram"},
            "llm_agent": {"agent_type": "simple_llm_agent"},
        }
    }
    tm.language = "en"
    tm.conversation_ended = False
    tm.hangup_triggered = False
    tm.function_call_in_flight = False
    tm.has_transfer = False
    tm.stream = True
    tm.response_in_pipeline = False
    tm._end_call_in_progress = False
    tm.transcriber_output_queue = asyncio.Queue()
    tm.process_transcriber_request = AsyncMock(return_value=0)
    tm._set_call_details = MagicMock()
    tm._get_next_step = MagicMock(return_value="llm")
    tm.tools = {"input": MagicMock(), "transcriber": MagicMock()}
    tm.tools["input"].welcome_message_played = MagicMock(return_value=True)
    tm.conversation_history = MagicMock()
    tm.conversation_history.is_duplicate_user = MagicMock(return_value=False)
    tm.interruption_manager = MagicMock()
    tm.interruption_manager.should_trigger_interruption = MagicMock(return_value=False)
    # Private cleanup is stubbed by default; tests that assert on it re-bind or
    # replace with AsyncMock and drive the real listener via tm.bind().
    tm.configure_mock(**{"_TaskManager__cleanup_downstream_tasks": AsyncMock()})

    for key, value in overrides.items():
        setattr(tm, key, value)
    return tm
