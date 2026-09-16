"""Behavior-parity tests for the moved agent-record helpers (spec 0002, step A3).

Mirrors every case of ``tests/test_platform_agent_records.py`` against the new home,
pins the legacy shim to the SAME function objects (an import-parity check stronger than
equal behavior), and covers the pure prompt-selection helpers that mirror the engine's
``task_<n>`` / multiagent ``task_1.{agent_name}.system_prompt`` read shape.
"""

from __future__ import annotations

import json

import pytest

from voiceai.modules.agents import static_methods
from voiceai.modules.agents.static_methods import (
    collect_agent_records,
    is_agent_key,
    parse_agent_record,
    select_multiagent_system_prompt,
    select_task_prompts,
    task_prompt_key,
)

AGENT = {"agent_name": "A", "agent_type": "voice", "tasks": []}
EXECUTION = {"execution_id": "exec_1", "status": "completed"}
MULTIAGENT_TASK_PROMPTS = {
    "support": {"system_prompt": "You are support."},
    "sales": {"system_prompt": "You are sales."},
}


def test_is_agent_key():
    """Bare UUID keys are agent records; ``:``-namespaced platform keys never are."""
    assert is_agent_key("3fff90ea-cc96-4ac0-a888-ef0718bf8628")
    assert not is_agent_key("platform:v1:executions:exec_1")
    assert not is_agent_key("platform:v1:idx:executions:agent:abc")


def test_parse_agent_record_accepts_agents_only():
    """Only bare-key JSON objects with a list ``tasks`` become records (legacy parity)."""
    assert parse_agent_record("uuid-1", json.dumps(AGENT)) == {"agent_id": "uuid-1", "data": AGENT}
    assert parse_agent_record("platform:v1:executions:exec_1", json.dumps(EXECUTION)) is None
    assert parse_agent_record("uuid-2", json.dumps({"agent_name": "No tasks"})) is None
    assert parse_agent_record("uuid-3", None) is None
    assert parse_agent_record("uuid-4", "not-json{{{") is None


def test_collect_preserves_id_alignment_with_skips():
    """Skipped pairs must never shift later records onto the wrong agent id."""
    pairs = [
        ("platform:v1:executions:exec_1", json.dumps(EXECUTION)),
        ("uuid-1", json.dumps(AGENT)),
        ("platform:v1:idx:executions:agent:abc", None),  # SET key: GET would raise
        ("uuid-2", json.dumps(AGENT)),
    ]

    records = collect_agent_records(pairs)

    assert [record["agent_id"] for record in records] == ["uuid-1", "uuid-2"]
    assert all(record["data"] == AGENT for record in records)


def test_legacy_shim_reexports_the_same_objects():
    """The old platform path must answer the SAME function objects, not copies.

    Identity (not equality) is the contract: a patch applied through either import path
    must be visible through the other, and the shim stays a pure re-export.
    """
    from voiceai.platform import agent_records as shim

    assert shim.is_agent_key is static_methods.is_agent_key
    assert shim.parse_agent_record is static_methods.parse_agent_record
    assert shim.collect_agent_records is static_methods.collect_agent_records


def test_task_prompt_key_is_one_based():
    """``task_manager.py:2149`` parity: zero-based task index, one-based storage key."""
    assert task_prompt_key(0) == "task_1"
    assert task_prompt_key(2) == "task_3"


def test_select_task_prompts_answers_block_or_none():
    """A present block passes through untouched; a missing one degrades to ``None``."""
    responses = {"task_1": {"system_prompt": "You are support.", "multilingual_prompts": {}}}

    assert select_task_prompts(responses, 0) == responses["task_1"]
    assert select_task_prompts(responses, 1) is None
    assert select_task_prompts({}, 0) is None


def test_select_multiagent_system_prompt_reads_the_legacy_shape():
    """The nested ``task_1.{agent_name}.system_prompt`` shape is read verbatim."""
    assert select_multiagent_system_prompt(MULTIAGENT_TASK_PROMPTS, "support") == "You are support."
    assert select_multiagent_system_prompt(MULTIAGENT_TASK_PROMPTS, "sales") == "You are sales."


def test_select_multiagent_system_prompt_raises_like_the_legacy_subscript():
    """A missing sub-agent raises ``KeyError`` exactly as ``prompts[agent]`` does."""
    with pytest.raises(KeyError):
        select_multiagent_system_prompt(MULTIAGENT_TASK_PROMPTS, "billing")
