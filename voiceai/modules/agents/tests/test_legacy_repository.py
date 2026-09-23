"""DI-fake tests for the redis definition adapter and the prompt-file store (spec 0002, A3).

Offline throughout: redis is a dict-backed fake injected through the constructor (rule 9),
and prompt-file IO runs against a ``tmp_path`` by monkeypatching the LEGACY module's
``PREPROCESS_DIR`` — deliberately the same patch target ``tests/test_agent_prompts_endpoint.py``
uses, because "existing monkeypatch targets keep intercepting" is itself the A3 contract
under test here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import voiceai.helpers.utils as legacy_utils
from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort
from voiceai.modules.agents.repository import FilePromptStore, RedisAgentRepository

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
AGENT_CONFIG = {"agent_name": "A", "agent_type": "voice", "tasks": []}
EXECUTION = {"execution_id": "exec_1", "status": "completed"}
PROMPTS = {"task_1": {"system_prompt": "You are support.", "multilingual_prompts": {}}}
MULTIAGENT_PROMPTS = {
    "task_1": {
        "support": {"system_prompt": "You are support."},
        "sales": {"system_prompt": "You are sales."},
    }
}
PROMPT_FILE_NAME = "conversation_details.json"


class FakeRedis:
    """Dict-backed conformer to the ``RedisLike`` slice, with per-key read failures.

    Args:
        records: Initial key → raw-string storage.
        wrongtype_keys: Keys that appear in ``keys()`` but raise on ``get()`` — the
            behavior of a redis SET (index) key read as a string.
    """

    def __init__(self, records: dict[str, str] | None = None, wrongtype_keys: set[str] | None = None) -> None:
        self.data: dict[str, str] = dict(records or {})
        self.wrongtype_keys: set[str] = set(wrongtype_keys or set())
        self.get_calls: list[str] = []

    async def get(self, name: str) -> str | None:
        """Record the read; raise for WRONGTYPE keys, else answer the stored string."""
        self.get_calls.append(name)
        if name in self.wrongtype_keys:
            raise RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return self.data.get(name)

    async def set(self, name: str, value: str) -> bool:
        """Store the raw string."""
        self.data[name] = value
        return True

    async def exists(self, *names: str) -> int:
        """Count stored keys among ``names``."""
        return sum(1 for name in names if name in self.data)

    async def delete(self, *names: str) -> int:
        """Drop ``names``; count removals."""
        return sum(1 for name in names if self.data.pop(name, None) is not None)

    async def keys(self, pattern: str) -> list[str]:
        """Answer every key — stored and WRONGTYPE — like a ``KEYS *`` scan."""
        return list(self.data) + sorted(self.wrongtype_keys - set(self.data))


@pytest.fixture
def prompt_dir(tmp_path, monkeypatch):
    """Point the LEGACY module's ``PREPROCESS_DIR`` at a tmp dir (the real patch target)."""
    monkeypatch.setattr(legacy_utils, "PREPROCESS_DIR", str(tmp_path))
    return tmp_path


def _write_prompt_file(root, agent_id, payload) -> None:
    directory = root / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PROMPT_FILE_NAME).write_text(json.dumps(payload))


# --- Port conformance ----------------------------------------------------------------------


def test_redis_repository_conforms_to_the_definition_port():
    """Structural conformance, at runtime and (via the annotation) under mypy."""
    port: AgentDefinitionPort = RedisAgentRepository(FakeRedis())

    assert isinstance(port, AgentDefinitionPort)


def test_prompt_store_conforms_to_the_session_store_port():
    """Structural conformance, at runtime and (via the annotation) under mypy."""
    port: AgentSessionStorePort = FilePromptStore()

    assert isinstance(port, AgentSessionStorePort)


# --- RedisAgentRepository ------------------------------------------------------------------


async def test_get_agent_answers_the_raw_dict_or_none():
    """The engine seam: raw config dicts out, ``None`` for a missing record."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: json.dumps(AGENT_CONFIG)}))

    assert await repository.get_agent(AGENT_ID) == AGENT_CONFIG
    assert await repository.get_agent("missing") is None


async def test_get_agent_treats_falsy_payload_as_absent():
    """legacy-parity: the quickstart guard is ``if not agent_data``, so ``""`` reads absent."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: ""}))

    assert await repository.get_agent(AGENT_ID) is None


async def test_get_agent_propagates_corrupt_json():
    """legacy-parity: a corrupt record raises out of the read (the 500-quirk's source)."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: "not-json{{{"}))

    with pytest.raises(json.JSONDecodeError):
        await repository.get_agent(AGENT_ID)


async def test_get_agent_model_is_the_validated_view_of_the_same_record():
    """Dual return: ``get_agent`` stays byte-identical raw; the model layers schema on top."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: json.dumps(AGENT_CONFIG)}))

    model = await repository.get_agent_model(AGENT_ID)

    assert isinstance(model, AgentModel)
    assert model.agent_name == AGENT_CONFIG["agent_name"]
    assert model.agent_type == AGENT_CONFIG["agent_type"]
    assert await repository.get_agent(AGENT_ID) == AGENT_CONFIG  # raw view untouched
    assert await repository.get_agent_model("missing") is None


async def test_get_agent_model_rejects_a_record_violating_the_schema():
    """A stored record without ``tasks`` fails validation as a module error, not pydantic's."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: json.dumps({"agent_name": "A"})}))

    with pytest.raises(AgentConfigInvalidError) as excinfo:
        await repository.get_agent_model(AGENT_ID)

    assert excinfo.value.details == {"agent_id": AGENT_ID}


async def test_save_agent_stores_the_quickstart_serialization():
    """The stored value is ``json.dumps(config)`` and round-trips through ``get_agent``."""
    fake = FakeRedis()
    repository = RedisAgentRepository(fake)

    await repository.save_agent(AGENT_ID, AGENT_CONFIG)

    assert fake.data[AGENT_ID] == json.dumps(AGENT_CONFIG)
    assert await repository.get_agent(AGENT_ID) == AGENT_CONFIG


async def test_delete_agent_reports_whether_a_record_existed():
    """``True`` once, then ``False`` — mirroring the exists-then-delete quickstart flow."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: json.dumps(AGENT_CONFIG)}))

    assert await repository.delete_agent(AGENT_ID) is True
    assert await repository.delete_agent(AGENT_ID) is False


async def test_delete_agent_orphans_the_prompt_file(prompt_dir):
    """legacy-parity: DELETE removes the redis record but never the prompt file."""
    repository = RedisAgentRepository(FakeRedis({AGENT_ID: json.dumps(AGENT_CONFIG)}))
    _write_prompt_file(prompt_dir, AGENT_ID, PROMPTS)

    assert await repository.delete_agent(AGENT_ID) is True

    assert (prompt_dir / AGENT_ID / PROMPT_FILE_NAME).exists()


async def test_list_agents_filters_platform_keys_before_the_get():
    """The verbatim scan: ``":"`` keys are never fetched, and IDs stay aligned."""
    fake = FakeRedis(
        {
            AGENT_ID: json.dumps(AGENT_CONFIG),
            "platform:v1:executions:exec_1": json.dumps(EXECUTION),
            "uuid-no-tasks": json.dumps({"agent_name": "No tasks"}),
            "uuid-2": json.dumps(AGENT_CONFIG),
        },
        wrongtype_keys={"platform:v1:idx:executions:agent:abc"},
    )
    repository = RedisAgentRepository(fake)

    records = await repository.list_agents()

    assert [record["agent_id"] for record in records] == [AGENT_ID, "uuid-2"]
    assert all(record["data"] == AGENT_CONFIG for record in records)
    assert all(":" not in fetched for fetched in fake.get_calls)


async def test_list_agents_survives_an_unreadable_bare_key():
    """A bare key that raises on GET is logged and skipped, never kills the scan."""
    fake = FakeRedis({AGENT_ID: json.dumps(AGENT_CONFIG)}, wrongtype_keys={"uuid-broken"})
    repository = RedisAgentRepository(fake)

    records = await repository.list_agents()

    assert [record["agent_id"] for record in records] == [AGENT_ID]
    assert "uuid-broken" in fake.get_calls  # bare keys ARE attempted; the failure is skipped


async def test_list_agents_answers_empty_for_an_empty_store():
    """``KEYS *`` finding nothing short-circuits to an empty directory."""
    repository = RedisAgentRepository(FakeRedis())

    assert await repository.list_agents() == []


# --- FilePromptStore -----------------------------------------------------------------------


async def test_prompt_round_trip_preserves_the_multiagent_shape(prompt_dir):
    """The ``task_1.{agent_name}.system_prompt`` nesting round-trips byte-identical."""
    store = FilePromptStore()

    await store.save_prompts(AGENT_ID, MULTIAGENT_PROMPTS)

    on_disk = json.loads((prompt_dir / AGENT_ID / PROMPT_FILE_NAME).read_text())
    assert on_disk == MULTIAGENT_PROMPTS
    assert await store.get_prompts(AGENT_ID) == MULTIAGENT_PROMPTS


async def test_get_prompts_missing_file_degrades_to_none(prompt_dir):
    """No stored prompts is not an error: the legacy loader degrades to ``None``."""
    store = FilePromptStore()

    assert await store.get_prompts(AGENT_ID) is None


async def test_save_prompts_none_stores_json_null(prompt_dir):
    """legacy-parity: ``None`` writes the literal ``null`` file, which reads back ``None``."""
    store = FilePromptStore()

    await store.save_prompts(AGENT_ID, None)

    assert (prompt_dir / AGENT_ID / PROMPT_FILE_NAME).read_text() == "null"
    assert await store.get_prompts(AGENT_ID) is None


async def test_prompt_io_goes_through_the_live_legacy_module_attributes(monkeypatch):
    """THE A3 seam contract: patches on ``voiceai.helpers.utils`` keep intercepting.

    The store must resolve ``store_file`` / ``get_prompt_responses`` as module attributes
    at call time — exactly what ``tests/test_agent_prompts_endpoint.py`` patches — so a
    function captured at import time (a dead patch target) would fail this test.
    """
    calls: dict[str, dict[str, Any]] = {}

    async def fake_store_file(**kwargs):
        calls["store_file"] = kwargs

    async def fake_get_prompt_responses(**kwargs):
        calls["get_prompt_responses"] = kwargs
        return PROMPTS

    monkeypatch.setattr(legacy_utils, "store_file", fake_store_file)
    monkeypatch.setattr(legacy_utils, "get_prompt_responses", fake_get_prompt_responses)
    store = FilePromptStore()

    await store.save_prompts(AGENT_ID, PROMPTS)
    read_back = await store.get_prompts(AGENT_ID)

    assert calls["store_file"] == {
        "file_key": f"{AGENT_ID}/{PROMPT_FILE_NAME}",
        "file_data": PROMPTS,
        "local": True,
    }
    assert calls["get_prompt_responses"] == {"assistant_id": AGENT_ID, "local": True}
    assert read_back == PROMPTS
