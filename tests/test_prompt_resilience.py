"""Prompt loader resilience: a missing or unreadable prompt file must never crash a call.

Contract (spec 0051): ``get_prompt_responses(agent_id, local=True)`` answers ``None`` — "no
stored prompts" — when ``agent_data/<id>/conversation_details.json`` is missing or
unreadable, never raises, and answers exactly the stored payload otherwise. Consumers
degrade ``None`` to empty prompts themselves; the engine seam is pinned by
``voiceai/modules/voice/tests/session/test_prompts.py::test_non_dict_payload_degrades_to_an_empty_system_prompt``.
"""

import json

import pytest

from voiceai.helpers import utils
from voiceai.modules.agents import utils as agents_utils

AGENT_ID = "agent-under-test"
PROMPT_FILE_NAME = "conversation_details.json"
STORED_PROMPTS = {"task_1": {"system_prompt": "Talk politely."}}


@pytest.fixture
def prompt_dir(tmp_path, monkeypatch):
    """Point the loader at an empty, disposable prompt directory."""
    monkeypatch.setattr(utils, "PREPROCESS_DIR", str(tmp_path))
    return tmp_path


def _write_prompt_file(prompt_dir, agent_id, text):
    agent_dir = prompt_dir / agent_id
    agent_dir.mkdir()
    (agent_dir / PROMPT_FILE_NAME).write_text(text)


async def test_missing_prompts_file_answers_none_without_raising(prompt_dir):
    assert await utils.get_prompt_responses(AGENT_ID, local=True) is None


async def test_unreadable_prompts_file_answers_none_without_raising(prompt_dir):
    _write_prompt_file(prompt_dir, AGENT_ID, "{not json")
    assert await utils.get_prompt_responses(AGENT_ID, local=True) is None


async def test_stored_prompts_file_answers_exactly_the_stored_payload(prompt_dir):
    _write_prompt_file(prompt_dir, AGENT_ID, json.dumps(STORED_PROMPTS))
    assert await utils.get_prompt_responses(AGENT_ID, local=True) == STORED_PROMPTS


async def test_agents_module_reader_answers_none_for_a_missing_agent(prompt_dir):
    # The typed seam (`dict | None`) the agents module documents: None means "no prompt file".
    assert await agents_utils.read_conversation_details(AGENT_ID) is None


async def test_agents_module_reader_answers_the_stored_payload_through_the_same_lookup_site(prompt_dir):
    # Without this round-trip the None pin above would also pass on "no such agent anywhere";
    # this proves the typed seam reads the very PREPROCESS_DIR the legacy loader reads.
    _write_prompt_file(prompt_dir, AGENT_ID, json.dumps(STORED_PROMPTS))
    assert await agents_utils.read_conversation_details(AGENT_ID) == STORED_PROMPTS
