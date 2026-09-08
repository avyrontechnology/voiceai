"""Unit tests for prompt loading resilience (missing file must not crash calls)."""

from voiceai.helpers import utils


async def test_missing_prompts_file_returns_empty_dict():
    out = await utils.get_prompt_responses("no-such-agent", local=True)
    assert out == {}


async def test_missing_prompts_result_supports_get():
    out = await utils.get_prompt_responses("no-such-agent", local=True)
    assert out.get("task_1") is None
