"""The moved runtime prompt loader (spec 0004, B6): behavior at the new home, seams pinned.

Three contracts under test, the B5 ``test_s2s_runner`` precedent. First, the Region-E
bodies behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.prompts`) against a plain stub session — the functions
take the session as their first parameter, so a duck-typed stub is the whole harness.
Second — the migration's load-bearing half — ``TaskManager`` keeps a SAME-NAMED thin
delegator per moved body (``load_prompt`` plus the three mangled privates), so the
assistant_manager fan-out, ``patch.object(TaskManager, ...)``, ``__new__`` harnesses and
internal self-dispatch keep resolving. Third, the prompts module is the lookup site for
the moved bodies' globals (``get_prompt_responses`` / ``structure_system_prompt`` / the
prompt constants — R3), and the B6 port seam (`prompt_responses_from_store` over
``AgentSessionStorePort``) feeds ``load_prompt`` byte-identically to the legacy fetch.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytz

import voiceai.helpers.utils as legacy_utils
import voiceai.prompts as legacy_prompts
from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session import prompts

UTC = pytz.timezone("UTC")

#: Every Region-E body the B6 contract moved; each keeps a TaskManager delegator.
MOVED_DELEGATORS = (
    "load_prompt",
    "_TaskManager__get_final_prompt",
    "_TaskManager__prefill_prompts",
    "_TaskManager__process_stop_words",
)

#: Names whose lookup site moved INTO the prompts module (string patches target it now).
PROMPTS_LOOKUP_SITES = (
    "get_prompt_responses",
    "structure_system_prompt",
    "update_prompt_with_context",
    "enrich_context_with_time_variables",
    "get_date_time_from_timezone",
    "FILLER_PROMPT",
    "DATE_PROMPT",
    "EXTRACTION_PROMPT",
    "SUMMARIZATION_PROMPT",
)


def _session(**overrides):
    """A duck-typed PromptSession stub; the loader takes it as its `self` parameter."""
    stub = SimpleNamespace(
        task_config={"task_type": "conversation", "tools_config": {}},
        context_data=None,
        timezone=UTC,
        is_local=False,
        assistant_id="agent-1",
        run_id="run-1",
        call_sid=None,
        is_web_based_call=False,
        use_fillers=False,
        prompts={},
        system_prompt={},
        prompt_map={},
        multilingual_prompts={},
        conversation_history=SimpleNamespace(setup_system_prompt=MagicMock()),
        language_switcher=None,
        language="en",
    )
    stub._is_conversation_task = lambda: stub.task_config["task_type"] == "conversation"
    stub._TaskManager__is_multiagent = lambda: False
    stub._TaskManager__is_knowledgebase_agent = lambda: False
    stub._TaskManager__apply_language_directive = MagicMock()
    # The bodies dispatch through `self._TaskManager__...` (which is how a patched
    # TaskManager delegator intercepts them); the stub binds the loader back the same way.
    stub._TaskManager__prefill_prompts = lambda task, prompt, task_type: prompts.prefill_prompts(
        stub, task, prompt, task_type
    )
    stub._TaskManager__get_final_prompt = lambda prompt, today, current_time, tz: prompts.get_final_prompt(
        stub, prompt, today, current_time, tz
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


def _deterministic_loader_globals(monkeypatch):
    """Pin the time-bearing globals so assembled prompts compare exactly."""
    monkeypatch.setattr(prompts, "get_date_time_from_timezone", lambda tz: ("DATE", "TIME"))
    monkeypatch.setattr(
        prompts,
        "structure_system_prompt",
        lambda sp, run_id, assistant_id, call_sid, context_data, timezone, web: f"STRUCTURED({sp})",
    )


# --- Lookup sites: the prompts module owns the moved bodies' globals (R3) ---


def test_prompts_module_is_the_lookup_site_for_the_moved_globals():
    for name in PROMPTS_LOOKUP_SITES:
        assert hasattr(prompts, name), f"prompts.{name} must exist as a patchable module global"


def test_lookup_site_globals_are_the_legacy_objects_by_identity():
    assert prompts.get_prompt_responses is legacy_utils.get_prompt_responses
    assert prompts.structure_system_prompt is legacy_utils.structure_system_prompt
    assert prompts.update_prompt_with_context is legacy_utils.update_prompt_with_context
    assert prompts.enrich_context_with_time_variables is legacy_utils.enrich_context_with_time_variables
    assert prompts.get_date_time_from_timezone is legacy_utils.get_date_time_from_timezone
    assert prompts.FILLER_PROMPT is legacy_prompts.FILLER_PROMPT
    assert prompts.DATE_PROMPT is legacy_prompts.DATE_PROMPT
    assert prompts.EXTRACTION_PROMPT is legacy_prompts.EXTRACTION_PROMPT
    assert prompts.SUMMARIZATION_PROMPT is legacy_prompts.SUMMARIZATION_PROMPT


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_body():
    for name in MOVED_DELEGATORS:
        assert callable(getattr(TaskManager, name, None)), f"TaskManager.{name} delegator missing"


async def test_load_prompt_delegator_injects_the_session():
    tm = cast(Any, TaskManager.__new__(TaskManager))  # why: bare __new__ harness, attrs untyped
    # patch.object sees the async def and swaps in an AsyncMock automatically.
    with patch.object(prompts, "load_prompt") as moved:
        moved.return_value = "done"
        result = await TaskManager.load_prompt(tm, "name", 0, True, extra=1)
    assert result == "done"
    moved.assert_called_once_with(tm, "name", 0, True, extra=1)


def test_sync_delegators_inject_the_session():
    tm = cast(Any, TaskManager.__new__(TaskManager))  # why: bare __new__ harness, attrs untyped
    with patch.object(prompts, "prefill_prompts", return_value="P") as prefill:
        assert tm._TaskManager__prefill_prompts({"t": 1}, "raw", "conversation") == "P"
    prefill.assert_called_once_with(tm, {"t": 1}, "raw", "conversation")

    with patch.object(prompts, "get_final_prompt", return_value="F") as final:
        assert tm._TaskManager__get_final_prompt("raw", "d", "t", UTC) == "F"
    final.assert_called_once_with(tm, "raw", "d", "t", UTC)

    with patch.object(prompts, "process_stop_words", return_value="S") as stop:
        assert tm._TaskManager__process_stop_words("chunk", {}) == "S"
    stop.assert_called_once_with(tm, "chunk", {})


# --- load_prompt behavior at the new home ---


async def test_webhook_task_returns_before_touching_anything():
    stub = _session(task_config={"task_type": "webhook", "tools_config": {}})
    stub.conversation_history.setup_system_prompt.side_effect = AssertionError("must not run")
    await prompts.load_prompt(stub, "name", 0, True)
    assert stub.system_prompt == {}


async def test_non_dict_payload_degrades_to_an_empty_system_prompt(monkeypatch):
    _deterministic_loader_globals(monkeypatch)

    async def fetch(**kwargs):
        return None  # missing conversation_details.json

    monkeypatch.setattr(prompts, "get_prompt_responses", fetch)
    stub = _session()
    await prompts.load_prompt(stub, "name", 0, True)
    assert stub.system_prompt == {"role": "system", "content": ""}
    stub.conversation_history.setup_system_prompt.assert_called_once_with(stub.system_prompt)


async def test_single_agent_system_prompt_assembles_the_exact_final_prompt(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {"task_1": {"system_prompt": "Talk politely."}}
    stub = _session(use_fillers=True)
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)

    expected_notes = f"### Note:\n1.{prompts.FILLER_PROMPT}\n"
    expected = f"\n## Agent Prompt:\n\nSTRUCTURED(Talk politely.)\n{expected_notes}\n\n## Transcript:\n"
    assert stub.system_prompt == {"role": "system", "content": expected}
    assert stub.prompts["system_prompt"] == expected
    stub.conversation_history.setup_system_prompt.assert_called_once_with(stub.system_prompt)


async def test_no_fillers_leaves_the_note_block_empty(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {"task_1": {"system_prompt": "Talk politely."}}
    stub = _session(use_fillers=False)
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)
    assert stub.system_prompt["content"] == "\n## Agent Prompt:\n\nSTRUCTURED(Talk politely.)\n\n\n## Transcript:\n"


async def test_call_sid_is_stamped_from_recipient_data(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {"task_1": {"system_prompt": "P"}}
    stub = _session(context_data={"recipient_data": {"call_sid": "CA123"}})
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)
    assert stub.call_sid == "CA123"


async def test_task_zero_pins_the_timezone_from_recipient_data(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    stub = _session(context_data={"recipient_data": {"timezone": "Asia/Kolkata"}})
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses={"task_1": {}})
    assert stub.timezone.zone == "Asia/Kolkata"


async def test_multiagent_builds_the_prompt_map_and_default_prompt(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {
        "task_1": {
            "alpha": {"system_prompt": "A-prompt"},
            "beta": {"system_prompt": "B-prompt"},
        }
    }
    stub = _session(
        task_config={
            "task_type": "conversation",
            "tools_config": {
                "llm_agent": {"llm_config": {"agent_map": {"alpha": {}, "beta": {}}, "default_agent": "beta"}}
            },
        }
    )
    stub._TaskManager__is_multiagent = lambda: True
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)

    assert set(stub.prompt_map) == {"alpha", "beta"}
    # get_final_prompt appends the note header and the DATE_PROMPT block.
    assert stub.prompt_map["beta"].startswith("B-prompt\n### Note:\n")
    # Preserved legacy quirk: the single-agent block below the multiagent branch sees
    # "system_prompt" missing from the (untouched) `prompts` dict and CLOBBERS the
    # default-agent system prompt back to empty content; prompt_map keeps the real
    # prompts and downstream generation resolves per-agent through it.
    assert stub.system_prompt == {"role": "system", "content": ""}
    stub.conversation_history.setup_system_prompt.assert_called_once_with({"role": "system", "content": ""})
    assert stub.multilingual_prompts == {}


async def test_multilingual_prompts_assemble_per_language(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {
        "task_1": {
            "system_prompt": "Base",
            "multilingual_prompts": {"hi": "Hindi base", "te": "Telugu base"},
        }
    }
    stub = _session()
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)
    assert set(stub.multilingual_prompts) == {"hi", "te"}
    assert stub.multilingual_prompts["hi"] == "\n## Agent Prompt:\n\nSTRUCTURED(Hindi base)\n\n\n## Transcript:\n"


async def test_language_directive_fires_only_with_a_switcher_and_content(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {"task_1": {"system_prompt": "Base"}}

    stub = _session(language_switcher=object(), language="hi")
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)
    stub._TaskManager__apply_language_directive.assert_called_once_with("hi")

    silent = _session(language_switcher=None)
    await prompts.load_prompt(silent, "name", 0, True, prompt_responses=payload)
    silent._TaskManager__apply_language_directive.assert_not_called()


async def test_knowledgebase_agent_gets_the_prompt_injected_into_its_config(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {"task_1": {"system_prompt": "KB base"}}
    stub = _session(
        task_config={
            "task_type": "conversation",
            "tools_config": {"llm_agent": {"llm_config": {}}},
        }
    )
    stub._TaskManager__is_knowledgebase_agent = lambda: True
    await prompts.load_prompt(stub, "name", 0, True, prompt_responses=payload)
    injected = stub.task_config["tools_config"]["llm_agent"]["llm_config"]["prompt"]
    assert injected == stub.system_prompt["content"]


# --- The port seam: AgentSessionStorePort feeds the same loader byte-identically ---


class _FakePromptStore:
    """An in-memory ``AgentSessionStorePort`` conformer."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get_prompts(self, agent_id):
        self.calls.append(agent_id)
        return self.payload

    async def save_prompts(self, agent_id, prompts_payload):
        raise AssertionError("the loader never writes")


async def test_prompt_responses_from_store_reads_through_the_port():
    store = _FakePromptStore({"task_1": {"system_prompt": "stored"}})
    assert await prompts.prompt_responses_from_store(store, "agent-9") == {"task_1": {"system_prompt": "stored"}}
    assert store.calls == ["agent-9"]


async def test_port_backed_payload_loads_identically_to_the_legacy_fetch(monkeypatch):
    _deterministic_loader_globals(monkeypatch)
    payload = {"task_1": {"system_prompt": "Same either way"}}

    async def legacy_fetch(**kwargs):
        assert kwargs == {"assistant_id": "agent-1", "local": True}
        return {"task_1": {"system_prompt": "Same either way"}}

    monkeypatch.setattr(prompts, "get_prompt_responses", legacy_fetch)
    via_legacy = _session()
    await prompts.load_prompt(via_legacy, "name", 0, True)

    async def poisoned_fetch(**kwargs):
        raise AssertionError("kwargs payload must bypass the legacy fetch")

    monkeypatch.setattr(prompts, "get_prompt_responses", poisoned_fetch)
    via_port = _session()
    port_payload = await prompts.prompt_responses_from_store(_FakePromptStore(payload), "agent-1")
    await prompts.load_prompt(via_port, "name", 0, True, prompt_responses=port_payload)

    assert via_port.system_prompt == via_legacy.system_prompt
    assert via_port.prompts == via_legacy.prompts
    assert via_port.multilingual_prompts == via_legacy.multilingual_prompts


# --- prefill_prompts / get_final_prompt / process_stop_words ---


def test_prefill_synthesizes_the_extraction_prompt_with_substituted_schema(monkeypatch):
    monkeypatch.setattr(prompts, "get_date_time_from_timezone", lambda tz: ("DATE", "TIME"))
    stub = _session(context_data={"recipient_data": {"customer": "Ravi"}})
    task = {"tools_config": {"llm_agent": {"llm_config": {"extraction_json": "name: {customer}"}}}}
    result = prompts.prefill_prompts(stub, task, None, "extraction")
    assert result == {"system_prompt": prompts.EXTRACTION_PROMPT.format("DATE", "TIME", UTC, "name: Ravi")}


def test_prefill_answers_the_summarization_prompt_and_passthrough():
    stub = _session()
    assert prompts.prefill_prompts(stub, {}, None, "summarization") == {"system_prompt": prompts.SUMMARIZATION_PROMPT}
    existing = {"system_prompt": "keep me"}
    assert prompts.prefill_prompts(stub, {}, existing, "extraction") is existing
    assert prompts.prefill_prompts(stub, {}, "prompt-as-str", "conversation") == "prompt-as-str"


def test_prefill_repins_the_timezone_from_recipient_data():
    stub = _session(context_data={"recipient_data": {"timezone": "Asia/Kolkata"}})
    prompts.prefill_prompts(stub, {}, {"system_prompt": "x"}, "conversation")
    assert stub.timezone.zone == "Asia/Kolkata"


def test_get_final_prompt_appends_notes_and_date_block():
    stub = _session(use_fillers=True, context_data=None)
    result = prompts.get_final_prompt(stub, "Base", "DATE", "TIME", UTC)
    assert result == f"Base\n### Note:\n1.{prompts.FILLER_PROMPT}\n\n{prompts.DATE_PROMPT.format('DATE', 'TIME', UTC)}"


def test_get_final_prompt_substitutes_context_variables():
    stub = _session(context_data={"recipient_data": {"customer": "Ravi"}})
    result = prompts.get_final_prompt(stub, "Hello {customer}", "DATE", "TIME", UTC)
    assert result.startswith("Hello Ravi\n### Note:\n")


def test_process_stop_words_trims_only_the_final_chunk():
    stub = _session()
    eos = {"end_of_llm_stream": True}
    assert prompts.process_stop_words(stub, "Sure thing user:", eos) == "Sure thing "
    assert prompts.process_stop_words(stub, "Sure thing user", eos) == "Sure thing "
    assert prompts.process_stop_words(stub, "Sure thing user:", {"end_of_llm_stream": False}) == "Sure thing user:"
    assert prompts.process_stop_words(stub, "Regular reply.", eos) == "Regular reply."
