"""Tests for agent-record filtering (shared Redis holds platform keys too)."""

import json

from voiceai.platform.agent_records import collect_agent_records, is_agent_key, parse_agent_record

AGENT = {"agent_name": "A", "agent_type": "voice", "tasks": []}
EXECUTION = {"execution_id": "exec_1", "status": "completed"}


def test_is_agent_key():
    assert is_agent_key("3fff90ea-cc96-4ac0-a888-ef0718bf8628")
    assert not is_agent_key("platform:v1:executions:exec_1")
    assert not is_agent_key("platform:v1:idx:executions:agent:abc")


def test_parse_agent_record_accepts_agents_only():
    assert parse_agent_record("uuid-1", json.dumps(AGENT)) == {"agent_id": "uuid-1", "data": AGENT}
    assert parse_agent_record("platform:v1:executions:exec_1", json.dumps(EXECUTION)) is None
    assert parse_agent_record("uuid-2", json.dumps({"agent_name": "No tasks"})) is None
    assert parse_agent_record("uuid-3", None) is None
    assert parse_agent_record("uuid-4", "not-json{{{") is None


def test_collect_preserves_id_alignment_with_skips():
    pairs = [
        ("platform:v1:executions:exec_1", json.dumps(EXECUTION)),
        ("uuid-1", json.dumps(AGENT)),
        ("platform:v1:idx:executions:agent:abc", None),  # SET key: GET would raise
        ("uuid-2", json.dumps(AGENT)),
    ]
    records = collect_agent_records(pairs)
    assert [record["agent_id"] for record in records] == ["uuid-1", "uuid-2"]
