"""DI-fake tests for `AgentService` and the A4 helpers delegation (spec 0002, A4).

Offline throughout: the definition store, the prompt store, the LLM port, and the env-var
guard are all constructor-injected fakes (rule 9). Every quickstart quirk the service must
preserve verbatim is pinned here by name: status injection, the create/update extraction
asymmetry, falsy-prompts-to-null, the prompt-file orphan on DELETE, and the 503 when redis
is unconfigured.

The helpers-delegation pins live here too (A4 owns `helpers.py` but no separate test file):
each delegated function must both behave as the legacy implementation does and stay
monkeypatch-transparent against the `voiceai.helpers.utils` attributes.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import pytest

import voiceai.helpers.utils as legacy_utils
from voiceai.common.errors import ConfigurationError, DependencyUnavailableError
from voiceai.modules.agents import helpers as agents_helpers
from voiceai.modules.agents.errors import AgentNotFoundError
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.ports import LlmPort
from voiceai.modules.agents.service import AgentService

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
EXTRACTION_SYSTEM_PROMPT = "You are a parsing assistant."
EXTRACTION_DETAILS = "1. Call Reason: why the user called"
GENERATED_EXTRACTION_JSON = '{"call_reason": "why the user called"}'
PROMPTS = {"task_1": {"system_prompt": "You are support.", "multilingual_prompts": {}}}

CONVERSATION_TASK = {
    "tools_config": {},
    "toolchain": {"execution": "sequential", "pipelines": []},
}
EXTRACTION_TASK = {
    "task_type": "extraction",
    "tools_config": {"llm_agent": {"extraction_details": EXTRACTION_DETAILS}},
    "toolchain": {"execution": "sequential", "pipelines": []},
}


class FakeDefinitionStore:
    """In-memory `AgentDefinitionPort` conformer with optional per-id read failures."""

    def __init__(self, records: dict[str, dict[str, Any]] | None = None, corrupt_ids: set[str] | None = None) -> None:
        self.records: dict[str, dict[str, Any]] = dict(records or {})
        self.corrupt_ids: set[str] = set(corrupt_ids or set())
        self.saved: list[tuple[str, dict[str, Any]]] = []

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Answer the stored config, `None`, or raise like a corrupt redis payload."""
        if agent_id in self.corrupt_ids:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self.records.get(agent_id)

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Record and store the write."""
        self.saved.append((agent_id, config))
        self.records[agent_id] = config

    async def delete_agent(self, agent_id: str) -> bool:
        """Drop the record; report whether one existed."""
        return self.records.pop(agent_id, None) is not None

    async def list_agents(self) -> list[dict[str, Any]]:
        """Answer the quickstart record shape for every stored agent."""
        return [{"agent_id": agent_id, "data": data} for agent_id, data in self.records.items()]


class FakePromptStore:
    """In-memory `AgentSessionStorePort` conformer that records every write."""

    def __init__(self, prompts: dict[str, Any] | None = None) -> None:
        self.stored: dict[str, dict[str, Any] | None] = dict(prompts or {})
        self.writes: list[tuple[str, dict[str, Any] | None]] = []

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Answer the stored payload or `None`."""
        return self.stored.get(agent_id)

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Record and store the write (possibly `None`)."""
        self.writes.append((agent_id, prompts))
        self.stored[agent_id] = prompts


class FakeLlm:
    """`LlmPort` conformer answering a fixed completion and recording each call."""

    def __init__(self, text: str = GENERATED_EXTRACTION_JSON) -> None:
        self.text = text
        self.calls: list[list[dict[str, Any]]] = []

    async def __call__(self, messages: list[dict[str, Any]]) -> str:
        """Record the messages and answer the canned text."""
        self.calls.append(messages)
        return self.text


class GuardSpy:
    """Records invocations of the UPDATE-only extraction-model guard; optionally raises."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    def __call__(self) -> None:
        """Count the call; raise the configured failure when one is set."""
        self.calls += 1
        if self.failure is not None:
            raise self.failure


def build_service(
    definitions: FakeDefinitionStore | None,
    prompt_store: FakePromptStore | None = None,
    llm: FakeLlm | None = None,
    guard: GuardSpy | None = None,
) -> AgentService:
    """Assemble a service around fakes, defaulting each collaborator."""
    return AgentService(
        definitions=definitions,
        prompt_store=prompt_store if prompt_store is not None else FakePromptStore(),
        extraction_llm=llm if llm is not None else FakeLlm(),
        require_extraction_model=guard if guard is not None else GuardSpy(),
        extraction_system_prompt=EXTRACTION_SYSTEM_PROMPT,
        logger=logging.getLogger("otobaai.test.agents"),
    )


def agent_model(*tasks: dict[str, Any]) -> AgentModel:
    """Validate a minimal agent definition around the given task dicts."""
    return AgentModel.model_validate({"agent_name": "Support", "tasks": list(tasks)})


def stored_record() -> dict[str, Any]:
    """A pre-existing stored agent record, quickstart-shaped."""
    return {"agent_name": "Support", "agent_type": "other", "tasks": []}


# --- Unconfigured definition store (REDIS_URL empty ⇒ no repository) -----------------------


async def test_every_method_answers_503_when_the_definition_store_is_unconfigured():
    """With redis `None` the service degrades to `DependencyUnavailableError`, never a crash."""
    service = build_service(definitions=None)
    model = agent_model(CONVERSATION_TASK)

    with pytest.raises(DependencyUnavailableError):
        await service.get_agent(AGENT_ID)
    with pytest.raises(DependencyUnavailableError):
        await service.get_agent_prompts(AGENT_ID)
    with pytest.raises(DependencyUnavailableError):
        await service.create_agent(model, None)
    with pytest.raises(DependencyUnavailableError):
        await service.update_agent(AGENT_ID, model, None)
    with pytest.raises(DependencyUnavailableError):
        await service.delete_agent(AGENT_ID)
    with pytest.raises(DependencyUnavailableError):
        await service.list_agents()


# --- get_agent -----------------------------------------------------------------------------


async def test_get_agent_answers_the_raw_stored_dict():
    """The engine seam: the stored dict comes back untouched."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    service = build_service(store)

    assert await service.get_agent(AGENT_ID) == stored_record()


async def test_get_agent_raises_the_domain_404_for_a_missing_record():
    """The service is domain-correct; the controller owns the legacy 500 swallow."""
    service = build_service(FakeDefinitionStore())

    with pytest.raises(AgentNotFoundError):
        await service.get_agent(AGENT_ID)


# --- create_agent --------------------------------------------------------------------------


async def test_create_seeds_status_and_persists_config_and_prompts():
    """legacy-parity: `assistant_status="seeding"` injected; config and prompts both written."""
    store = FakeDefinitionStore()
    prompt_store = FakePromptStore()
    service = build_service(store, prompt_store)

    result = await service.create_agent(agent_model(CONVERSATION_TASK), PROMPTS)

    agent_id = result["agent_id"]
    assert UUID(agent_id)  # bare-UUID key contract
    assert result == {"agent_id": agent_id, "state": "created"}
    assert store.records[agent_id]["assistant_status"] == "seeding"
    assert store.records[agent_id]["agent_name"] == "Support"
    assert prompt_store.stored[agent_id] == PROMPTS


async def test_create_stores_none_prompts_as_none():
    """An omitted prompt payload is written through as `None` (stored as JSON null)."""
    prompt_store = FakePromptStore()
    service = build_service(FakeDefinitionStore(), prompt_store)

    result = await service.create_agent(agent_model(CONVERSATION_TASK), None)

    assert prompt_store.writes == [(result["agent_id"], None)]


async def test_create_generates_extraction_json_without_the_env_guard():
    """legacy-parity: create calls the LLM with system+details and never the UPDATE guard."""
    store = FakeDefinitionStore()
    llm = FakeLlm()
    guard = GuardSpy(failure=ConfigurationError("must not be called"))
    service = build_service(store, llm=llm, guard=guard)

    result = await service.create_agent(agent_model(EXTRACTION_TASK), None)

    assert guard.calls == 0
    assert llm.calls == [
        [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": EXTRACTION_DETAILS},
        ]
    ]
    stored_task = store.records[result["agent_id"]]["tasks"][0]
    assert stored_task["tools_config"]["llm_agent"]["extraction_json"] == GENERATED_EXTRACTION_JSON


async def test_create_leaves_non_extraction_tasks_alone():
    """A conversation task triggers no LLM call and gains no extraction_json."""
    store = FakeDefinitionStore()
    llm = FakeLlm()
    service = build_service(store, llm=llm)

    result = await service.create_agent(agent_model(CONVERSATION_TASK), None)

    assert llm.calls == []
    assert "extraction_json" not in str(store.records[result["agent_id"]]["tasks"][0])


# --- update_agent --------------------------------------------------------------------------


async def test_update_requires_an_existing_record():
    """A missing agent raises the domain 404 before anything is generated or written."""
    store = FakeDefinitionStore()
    service = build_service(store)

    with pytest.raises(AgentNotFoundError):
        await service.update_agent(AGENT_ID, agent_model(CONVERSATION_TASK), None)
    assert store.saved == []


async def test_update_injects_updated_status_and_overwrites():
    """legacy-parity: `assistant_status="updated"`; config and prompts both rewritten."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    prompt_store = FakePromptStore()
    service = build_service(store, prompt_store)

    result = await service.update_agent(AGENT_ID, agent_model(CONVERSATION_TASK), PROMPTS)

    assert result == {"agent_id": AGENT_ID, "state": "updated"}
    assert store.records[AGENT_ID]["assistant_status"] == "updated"
    assert prompt_store.stored[AGENT_ID] == PROMPTS


async def test_update_guards_the_extraction_model_before_generating():
    """legacy-parity: the UPDATE-only env guard runs, then the LLM, then the write."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    llm = FakeLlm()
    guard = GuardSpy()
    service = build_service(store, llm=llm, guard=guard)

    await service.update_agent(AGENT_ID, agent_model(EXTRACTION_TASK), None)

    assert guard.calls == 1
    assert llm.calls[0][1] == {"role": "user", "content": EXTRACTION_DETAILS}
    assert store.records[AGENT_ID]["tasks"][0]["tools_config"]["llm_agent"]["extraction_json"] == (
        GENERATED_EXTRACTION_JSON
    )


async def test_update_with_unconfigured_model_stops_before_the_llm_and_the_write():
    """legacy-parity: the guard failure surfaces as a 500 with no generation and no write."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    llm = FakeLlm()
    guard = GuardSpy(failure=ConfigurationError("Extraction model not configured"))
    service = build_service(store, llm=llm, guard=guard)

    with pytest.raises(ConfigurationError):
        await service.update_agent(AGENT_ID, agent_model(EXTRACTION_TASK), None)
    assert llm.calls == []
    assert store.saved == []


async def test_update_reads_extraction_details_with_a_get_default():
    """legacy-parity: update reads `.get("extraction_details", "")` — the schema dump always
    carries the key, so an unset brief passes through as `None`, exactly as quickstart sends it."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    llm = FakeLlm()
    service = build_service(store, llm=llm)
    task = {
        "task_type": "extraction",
        "tools_config": {"llm_agent": {"agent_flow_type": "streaming"}},
        "toolchain": {"execution": "sequential", "pipelines": []},
    }

    await service.update_agent(AGENT_ID, agent_model(task), None)

    assert llm.calls[0][1] == {"role": "user", "content": None}


# --- get_agent_prompts ---------------------------------------------------------------------


async def test_prompts_round_trip_and_missing_agent_404():
    """The quickstart shape `{"agent_id", "agent_prompts"}`; a missing agent is a true 404."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    prompt_store = FakePromptStore({AGENT_ID: PROMPTS})
    service = build_service(store, prompt_store)

    assert await service.get_agent_prompts(AGENT_ID) == {"agent_id": AGENT_ID, "agent_prompts": PROMPTS}
    with pytest.raises(AgentNotFoundError):
        await service.get_agent_prompts("missing")


@pytest.mark.parametrize("stored_prompts", [None, {}])
async def test_prompts_falsy_payloads_answer_null(stored_prompts):
    """legacy-parity: the quickstart guard is `if not prompts`, so `{}` also answers null."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    prompt_store = FakePromptStore({AGENT_ID: stored_prompts})
    service = build_service(store, prompt_store)

    result = await service.get_agent_prompts(AGENT_ID)

    assert result == {"agent_id": AGENT_ID, "agent_prompts": None}


async def test_prompts_served_even_when_the_stored_record_is_unparseable():
    """legacy-parity: the quickstart prompts route never parses the record, only reads it."""
    store = FakeDefinitionStore(corrupt_ids={AGENT_ID})
    prompt_store = FakePromptStore({AGENT_ID: PROMPTS})
    service = build_service(store, prompt_store)

    assert await service.get_agent_prompts(AGENT_ID) == {"agent_id": AGENT_ID, "agent_prompts": PROMPTS}


# --- delete_agent --------------------------------------------------------------------------


async def test_delete_removes_the_definition_but_orphans_the_prompts():
    """legacy-parity: the prompt payload survives a DELETE (the orphan quirk, preserved)."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})
    prompt_store = FakePromptStore({AGENT_ID: PROMPTS})
    service = build_service(store, prompt_store)

    result = await service.delete_agent(AGENT_ID)

    assert result == {"agent_id": AGENT_ID, "state": "deleted"}
    assert AGENT_ID not in store.records
    assert prompt_store.stored[AGENT_ID] == PROMPTS  # deliberately NOT removed


async def test_delete_missing_agent_raises_the_domain_404():
    """The service is domain-correct; the controller owns the legacy 500 swallow."""
    service = build_service(FakeDefinitionStore())

    with pytest.raises(AgentNotFoundError):
        await service.delete_agent(AGENT_ID)


# --- list_agents ---------------------------------------------------------------------------


async def test_list_agents_answers_the_quickstart_directory_shape():
    """`{"agents": [{"agent_id", "data"}, ...]}` — empty list when nothing is stored."""
    store = FakeDefinitionStore({AGENT_ID: stored_record()})

    assert await build_service(store).list_agents() == {
        "agents": [{"agent_id": AGENT_ID, "data": stored_record()}]
    }
    assert await build_service(FakeDefinitionStore()).list_agents() == {"agents": []}


# --- LlmPort conformance -------------------------------------------------------------------


def test_fake_llm_conforms_to_the_llm_port():
    """Structural conformance, at runtime and (via the annotation) under mypy."""
    port: LlmPort = FakeLlm()

    assert isinstance(port, LlmPort)
    assert not isinstance(object(), LlmPort)


def test_the_litellm_adapter_function_conforms_to_the_llm_port():
    """The §3.1 adapter function is itself an `LlmPort` (never invoked here: offline suite)."""
    from voiceai.modules.agents.adapters.llm import generate_extraction_text

    port: LlmPort = generate_extraction_text

    assert isinstance(port, LlmPort)


def test_the_adapter_guard_raises_only_when_the_model_env_is_unset(monkeypatch):
    """The UPDATE-only guard mirrors the quickstart `if not os.getenv(...)` check."""
    from voiceai.modules.agents.adapters.llm import ensure_extraction_model_configured

    monkeypatch.delenv("EXTRACTION_PROMPT_GENERATION_MODEL", raising=False)
    with pytest.raises(ConfigurationError):
        ensure_extraction_model_configured()

    monkeypatch.setenv("EXTRACTION_PROMPT_GENERATION_MODEL", "gpt-4.1-mini")
    ensure_extraction_model_configured()  # must not raise


# --- helpers.py delegation (A4 owns helpers; pinned here, no separate file this step) ------


def test_render_prompt_delegates_with_legacy_semantics():
    """Substitution and the `missing` default flow through to the legacy renderer."""
    assert agents_helpers.render_prompt("Hi {name}!", {"name": "Puneet"}) == "Hi Puneet!"
    assert agents_helpers.render_prompt("Hi {absent}!", {"name": "Puneet"}) == "Hi !"
    assert agents_helpers.render_prompt("{a}", {}, missing=None) == "{a}"


def test_update_prompt_with_context_delegates_and_strips_server_ids():
    """The server-owned call identifiers never reach the rendered prompt."""
    context = {"recipient_data": {"name": "Rahul", "call_sid": "CA123"}}

    rendered = agents_helpers.update_prompt_with_context("Hi {name}, ref {call_sid}.", context)

    assert rendered == "Hi Rahul, ref ."


def test_enrich_context_with_time_variables_delegates_and_mutates_in_place():
    """The time variables land in `recipient_data`; a `None` context is a no-op."""
    context: dict[str, Any] = {"recipient_data": {}}

    agents_helpers.enrich_context_with_time_variables(context, "UTC")

    assert context["recipient_data"]["timezone"] == "UTC"
    assert set(context["recipient_data"]) >= {"current_date", "current_time", "current_hour"}
    agents_helpers.enrich_context_with_time_variables(None, "UTC")  # must not raise


@pytest.mark.parametrize(
    "helper_name",
    ["render_prompt", "update_prompt_with_context", "enrich_context_with_time_variables", "structure_system_prompt"],
)
def test_helpers_delegation_is_monkeypatch_transparent(monkeypatch, helper_name):
    """Patching the LEGACY attribute intercepts the agents-module call — the A4 contract."""
    seen: list[tuple] = []

    def interceptor(*args: object, **kwargs: object) -> str:
        seen.append(args)
        return "intercepted"

    monkeypatch.setattr(legacy_utils, helper_name, interceptor)
    delegated = getattr(agents_helpers, helper_name)
    argument_count = {"render_prompt": 3, "update_prompt_with_context": 2}.get(helper_name)
    if helper_name == "enrich_context_with_time_variables":
        assert delegated({}, "UTC") is None
    elif helper_name == "structure_system_prompt":
        assert delegated("p", "run", "agent", "sid", None, "UTC") == "intercepted"
    else:
        assert delegated(*[None] * (argument_count or 0)) == "intercepted"

    assert len(seen) == 1
