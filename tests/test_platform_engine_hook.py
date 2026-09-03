"""Tests for the engine -> execution-log bridge."""

from enum import Enum

import pytest

from voiceai.platform.engine_hook import (
    history_to_transcript,
    merge_extracted_data,
    record_engine_execution,
)
from voiceai.platform.store import MemoryStore


class FakeRole(str, Enum):
    ASSISTANT = "assistant"
    USER = "user"
    SYSTEM = "system"


def test_history_to_transcript_maps_and_skips():
    messages = [
        {"role": "system", "content": "be nice"},
        {"role": FakeRole.ASSISTANT, "content": "Hello!"},
        {"role": FakeRole.USER, "content": "Hi there"},
        {"role": "tool", "content": "fn()"},
        {"role": "user", "content": "   "},
    ]
    turns = history_to_transcript(messages)
    assert [(t.role, t.text) for t in turns] == [("agent", "Hello!"), ("user", "Hi there")]
    assert turns[1].ts > turns[0].ts


def test_history_to_transcript_empty():
    assert history_to_transcript(None) == []
    assert history_to_transcript([]) == []


def test_merge_extracted_data():
    outputs = [{"extracted_data": {"a": 1}}, {"other": True}, {"extracted_data": {"b": 2}}]
    assert merge_extracted_data(outputs) == {"a": 1, "b": 2}
    assert merge_extracted_data(None) == {}


async def test_record_engine_execution_persists():
    store = MemoryStore()
    execution = await record_engine_execution(
        store,
        agent_id="agent-1",
        run_id="run-1",
        history=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi!"}],
        task_outputs=[{"extracted_data": {"intent": "greeting"}}],
        to_number="+91111",
    )
    assert execution is not None
    assert execution.execution_id == "run-1"
    assert execution.status.value == "completed"
    assert len(execution.transcript) == 2
    assert execution.extracted_data == {"intent": "greeting"}
    assert (await store.get_execution("run-1")) is not None


async def test_record_engine_execution_never_raises():
    class BrokenStore(MemoryStore):
        async def save_execution(self, execution):
            raise RuntimeError("redis down")

    assert await record_engine_execution(BrokenStore(), agent_id="a") is None
    assert await record_engine_execution(None, agent_id="a") is None
