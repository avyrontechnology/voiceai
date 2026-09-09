"""Browser init must always lead to the welcome message (cascaded pipeline).

Regression: a playground Talk leg sends {type:init, meta_data:{agent_id, source}}
with no context_data, and TaskManager.context_data is None for agents stored
with null input/output. handle_init_event crashed on
``self.context_data["recipient_data"].update(init_meta_data["context_data"])``
('NoneType' object is not subscriptable) BEFORE send_init_acknowledgement and
__first_message, so the welcome never played and every later transcript was
dropped with reason=welcome_still_playing — total silence despite working STT.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager


def _tm(*, context_data=None):
    tm = TaskManager.__new__(TaskManager)
    tm.context_data = context_data
    tm.prompts = {"system_prompt": "Hello"}
    tm.system_prompt = {"content": "Sys prompt"}
    history = MagicMock()
    history.__len__ = MagicMock(return_value=0)
    tm.conversation_history = history
    tm.call_hangup_message_config = None
    tm.kwargs = {"agent_welcome_message": "Hello! Is this a good time?"}
    tm.tools = {"output": SimpleNamespace(send_init_acknowledgement=AsyncMock())}
    tm._TaskManager__first_message = AsyncMock()
    return tm


async def test_init_without_context_data_still_sends_welcome():
    tm = _tm(context_data=None)
    await tm.handle_init_event({"agent_id": "a", "source": "ui-live-talk"})

    tm.tools["output"].send_init_acknowledgement.assert_awaited_once()
    assert tm.first_message_task is not None
    await asyncio.wait_for(tm.first_message_task, timeout=5)
    tm._TaskManager__first_message.assert_awaited_once()
    assert isinstance(tm.context_data, dict)
    assert isinstance(tm.context_data.get("recipient_data"), dict)


async def test_init_with_context_data_merges_and_sends_welcome():
    tm = _tm(context_data={"recipient_data": {"a": 1}})
    await tm.handle_init_event({"context_data": {"b": 2}})

    assert tm.context_data["recipient_data"] == {"a": 1, "b": 2}
    tm.tools["output"].send_init_acknowledgement.assert_awaited_once()
    assert tm.first_message_task is not None
    await asyncio.wait_for(tm.first_message_task, timeout=5)
    tm._TaskManager__first_message.assert_awaited_once()
