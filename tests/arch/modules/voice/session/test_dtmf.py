"""The moved DTMF consumer (spec 0004, B8): the digit-queue loop at its new home.

Two contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_health`` precedent.
First, ``inject_digits_to_conversation`` behaves concretely when driven through its
NEW module (`voiceai.modules.voice.session.dtmf`) against a plain stub session:
digits land in the ledger with their conversation offset, each burst becomes a
``dtmf_number:``-prefixed LLM turn with fresh metadata, one bad burst never kills
the consumer, and cancellation still tears it down. Second, ``TaskManager`` keeps
the SAME-NAMED thin delegator that injects the session (self), so ``__init__``'s
``asyncio.create_task(self.inject_digits_to_conversation())`` scheduling — behind
the tm:697 single-consumer guard, which stays at that call site — keeps resolving."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session import dtmf


def _session(meta=None, **overrides):
    stub = SimpleNamespace(
        queues={"dtmf": asyncio.Queue()},
        dtmf_events=[],
        conversation_start_init_ts=time.time() * 1000 - 5000,
        tools={"input": SimpleNamespace(io_provider="twilio")},
        _TaskManager__get_updated_meta_info=MagicMock(return_value=meta if meta is not None else {"sequence_id": 7}),
        _handle_transcriber_output=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def _drive(stub, bursts, settle=0.05):
    """Run the consumer as a task, feed it bursts, then cancel it."""
    task = asyncio.create_task(dtmf.inject_digits_to_conversation(stub))
    for burst in bursts:
        await stub.queues["dtmf"].put(burst)
    await asyncio.sleep(settle)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return task


# --- Delegator: TaskManager keeps the moved name and injects itself ---


def test_task_manager_keeps_the_same_named_delegator():
    assert callable(TaskManager.inject_digits_to_conversation)


async def test_the_delegator_injects_the_session_into_the_dtmf_module(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(dtmf, "inject_digits_to_conversation", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm.inject_digits_to_conversation()
    moved.assert_awaited_once_with(tm)


# --- Behavior at the new home ---


async def test_a_digit_burst_becomes_a_prefixed_llm_turn():
    stub = _session()
    await _drive(stub, ["12"])
    stub._TaskManager__get_updated_meta_info.assert_called_once_with(
        {"io": "twilio", "type": "text", "sequence": 0, "origin": "dtmf"}
    )
    stub._handle_transcriber_output.assert_awaited_once_with("llm", "dtmf_number: 12", {"sequence_id": 7})


async def test_each_digit_lands_in_the_ledger_with_the_burst_offset():
    stub = _session()
    await _drive(stub, ["12"])
    assert [event["digit"] for event in stub.dtmf_events] == ["1", "2"]
    # One timestamp per burst, stamped as ms since conversation start (~5s here).
    offsets = {event["ts_ms"] for event in stub.dtmf_events}
    assert len(offsets) == 1
    assert 4000 < offsets.pop() < 10000


async def test_one_bad_burst_never_kills_the_consumer():
    stub = _session()
    stub._TaskManager__get_updated_meta_info = MagicMock(
        side_effect=[RuntimeError("meta exploded"), {"sequence_id": 9}]
    )
    await _drive(stub, ["3", "4"])
    # The first burst dies inside the loop's isolation; the second still lands.
    stub._handle_transcriber_output.assert_awaited_once_with("llm", "dtmf_number: 4", {"sequence_id": 9})
    assert [event["digit"] for event in stub.dtmf_events] == ["3", "4"]


async def test_cancellation_tears_the_consumer_down():
    stub = _session()
    task = await _drive(stub, [])
    assert task.cancelled()  # CancelledError is not swallowed by the per-burst isolation
    stub._handle_transcriber_output.assert_not_awaited()
