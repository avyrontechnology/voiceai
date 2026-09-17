"""Runtime prompt loading: Region E of the legacy TaskManager (spec 0004, B6).

The prompt-loading bodies — final-prompt enrichment, ``load_prompt`` itself, the
extraction/summarization prefill, and the small-model stop-word trim — moved here
VERBATIM from ``voiceai/agent_manager/task_manager.py`` (original lines 2111-2276).
The B5 ``s2s_runner`` seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 (kwargs-injection precedent) — so this module imports no
  legacy engine code. `PromptSession` is the typed facade of exactly what the loader
  touches.
* **Same-named delegators stay on TaskManager.** ``load_prompt`` and the three private
  bodies keep thin same-named delegators on the class, so the assistant_manager fan-out,
  ``patch.object(TaskManager, ...)``, ``__new__`` harnesses and internal self-dispatch
  (``__process_stop_words`` at the LLM output site) keep resolving.
* **This module is the lookup site.** ``get_prompt_responses``,
  ``structure_system_prompt`` and the prompt constants are bound into THIS module's
  globals via ``adapters.prompt_runtime`` (§3.1 bridge 1), so monkeypatch string paths
  target ``voiceai.modules.voice.session.prompts.<name>`` (R3).

**The port seam (this step's "over AgentDefinitionPort").** The stored prompt payload
is the agents module's territory: `prompt_responses_from_store` types the fetch over
``AgentSessionStorePort`` (spec 0002 split the definition CRUD — ``AgentDefinitionPort``
— from the prompt payload store; the payload port is the store one), imported through
the agents package's public ``__all__`` surface (§3.1 bridge 4). ``load_prompt`` already
accepts the payload through its ``prompt_responses`` kwarg, so composition (B13a) wires
the port by passing ``await prompt_responses_from_store(store, agent_id)`` — the legacy
``get_prompt_responses`` branch below then never fires and retires with the bridge.

Four compile-time name-mangling accommodations inside otherwise-verbatim bodies (the
B5 precedent — the bodies no longer live in a class named ``TaskManager``):
``self.__prefill_prompts`` / ``self.__get_final_prompt`` / ``self.__is_multiagent`` /
``self.__apply_language_directive`` / ``self.__is_knowledgebase_agent`` are spelled
``self._TaskManager__<name>``, which is exactly what the class body always compiled to
— and it keeps a patched TaskManager delegator intercepting internal dispatch.
Signatures gained type annotations (rule 6), public functions gained docstrings
(rule 7), and the module logs through ``otobaai`` (rule 3; log content preserved —
including the full-prompt INFO line, a preserved PII quirk owned by
``revamp/resilient-core``: TODO(spec-0005) move it to DEBUG at the platform strangler).
"""

from __future__ import annotations

from typing import Any, Protocol

import pytz

from voiceai.common.logger import get_logger
from voiceai.modules.agents import AgentSessionStorePort
from voiceai.modules.voice.adapters.prompt_runtime import (
    DATE_PROMPT,
    EXTRACTION_PROMPT,
    FILLER_PROMPT,
    SUMMARIZATION_PROMPT,
    enrich_context_with_time_variables,
    get_date_time_from_timezone,
    get_prompt_responses,
    structure_system_prompt,
    update_prompt_with_context,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "DATE_PROMPT",
    "EXTRACTION_PROMPT",
    "FILLER_PROMPT",
    "PromptSession",
    "SUMMARIZATION_PROMPT",
    "enrich_context_with_time_variables",
    "get_date_time_from_timezone",
    "get_final_prompt",
    "get_prompt_responses",
    "load_prompt",
    "prefill_prompts",
    "process_stop_words",
    "prompt_responses_from_store",
    "structure_system_prompt",
    "update_prompt_with_context",
]


class PromptSession(Protocol):
    """The narrow facade of the live call session the prompt loader drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the legacy instance state the
    moved bodies read and write; the ``_TaskManager__*`` members are the session's
    own private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` intercepts internal dispatch too.
    """

    # --- call identity / config ---
    task_config: dict
    assistant_id: Any  # why: legacy id attr, str or None
    run_id: Any  # why: legacy execution id, str or None
    call_sid: Any  # why: stamped from context_data mid-load, str or None
    context_data: Any  # why: free-form recipient context dict, or None
    timezone: Any  # why: a pytz timezone object, reassigned from recipient data
    is_local: bool
    is_web_based_call: bool
    use_fillers: Any  # why: legacy truthy flag

    # --- prompt state the loader writes ---
    prompts: Any  # why: dict of task prompts, or the raw prefill passthrough
    system_prompt: Any  # why: the {"role": "system", "content": ...} dict
    prompt_map: dict
    multilingual_prompts: dict

    # --- collaborators ---
    conversation_history: Any  # why: legacy ConversationHistory
    language_switcher: Any  # why: legacy LanguageSwitcher, or None
    language: Any  # why: legacy language code attr/property

    def _is_conversation_task(self) -> bool:
        """True when the task is the realtime conversation leg."""
        ...

    def _TaskManager__is_multiagent(self) -> bool:
        """True when the llm_agent is the multiagent graph."""
        ...

    def _TaskManager__is_knowledgebase_agent(self) -> bool:
        """True when the llm_agent is the knowledgebase agent."""
        ...

    def _TaskManager__apply_language_directive(self, label: Any) -> None:
        """Pin the starting language directive onto the system prompt."""
        ...

    def _TaskManager__prefill_prompts(self, task: Any, prompt: Any, task_type: Any) -> Any:
        """The session's prefill delegator (this module's `prefill_prompts`)."""
        ...

    def _TaskManager__get_final_prompt(self, prompt: Any, today: Any, current_time: Any, current_timezone: Any) -> str:
        """The session's final-prompt delegator (this module's `get_final_prompt`)."""
        ...


async def prompt_responses_from_store(store: AgentSessionStorePort, agent_id: str) -> dict[str, Any] | None:
    """Fetch the raw prompt payload through the agents module's port seam.

    This is the B6 port face of the loader: composition (B13a) resolves the store from
    the container and feeds the result into `load_prompt` through its existing
    ``prompt_responses`` kwarg, which retires the legacy ``get_prompt_responses``
    branch. A missing payload answers ``None`` — `load_prompt` then degrades to empty
    prompts exactly as it always has for a non-dict fetch.

    Args:
        store: The agents module's prompt-payload port.
        agent_id: The bare-UUID agent id whose prompts to read.

    Returns:
        The stored payload, or ``None`` when none was saved.
    """
    return await store.get_prompts(agent_id)


def get_final_prompt(self: PromptSession, prompt: Any, today: Any, current_time: Any, current_timezone: Any) -> str:
    """Enrich one agent prompt with context, the filler note and the date suffix.

    Verbatim Region E body (multiagent per-agent path). Mutates ``context_data`` in
    place with current-time variables before substitution, exactly as the legacy
    helper always did.

    Args:
        self: The live call session (the legacy TaskManager injects itself).
        prompt: The raw stored agent prompt.
        today: Date string from `get_date_time_from_timezone`.
        current_time: Time string from `get_date_time_from_timezone`.
        current_timezone: The session's pytz timezone.

    Returns:
        The final prompt text with note and date blocks appended.
    """
    enriched_prompt = prompt
    if self.context_data is not None:
        enrich_context_with_time_variables(self.context_data, current_timezone)
        enriched_prompt = update_prompt_with_context(enriched_prompt, self.context_data)
    notes = "### Note:\n"
    if self._is_conversation_task() and self.use_fillers:
        notes += f"1.{FILLER_PROMPT}\n"
    return f"{enriched_prompt}\n{notes}\n{DATE_PROMPT.format(today, current_time, current_timezone)}"


async def load_prompt(self: PromptSession, assistant_name: Any, task_id: int, local: bool, **kwargs: Any) -> None:
    """Load, enrich and install the task's system prompt(s) on the session.

    Verbatim Region E body: webhook tasks return untouched; the payload comes from the
    ``prompt_responses`` kwarg (the B13a port seam) or the legacy fetch; a non-dict
    payload degrades to empty prompts; multiagent tasks build ``prompt_map``,
    everything else lands on ``system_prompt`` (and the multilingual map), which is
    then pinned onto the conversation history and — for LID-switch calls — stamped
    with the starting-language directive.

    Args:
        self: The live call session (the legacy TaskManager injects itself).
        assistant_name: Unused legacy parameter, preserved for the call contract.
        task_id: Zero-based task index; selects the ``task_{n+1}`` payload block.
        local: Whether the legacy fetch reads the local prompt file (else S3).
        **kwargs: Optional ``prompt_responses`` payload injection.

    Raises:
        KeyError: When a multiagent payload lacks a mapped agent's prompts (the
            legacy surface — preserved, never smoothed here).
    """
    if self.task_config["task_type"] == "webhook":
        return

    agent_type = (self.task_config["tools_config"].get("llm_agent") or {}).get("agent_type", "simple_llm_agent")  # noqa: F841 — verbatim legacy dead local (R8)
    self.is_local = local
    if task_id == 0:
        if (
            self.context_data
            and "recipient_data" in self.context_data
            and self.context_data["recipient_data"]
            and self.context_data["recipient_data"].get("timezone", None)
        ):
            self.timezone = pytz.timezone(self.context_data["recipient_data"]["timezone"])
    current_date, current_time = get_date_time_from_timezone(self.timezone)

    prompt_responses = kwargs.get("prompt_responses", None)
    if not prompt_responses:
        prompt_responses = await get_prompt_responses(assistant_id=self.assistant_id, local=self.is_local)
    if not isinstance(prompt_responses, dict):
        # No stored prompts (missing file, fresh record): degrade to an empty
        # system prompt rather than crashing the call on .get().
        logger.error(
            f"No usable prompt responses for {self.assistant_id} "
            f"(got {type(prompt_responses).__name__}); continuing with an empty system prompt."
        )
        prompt_responses = {}

    current_task = "task_{}".format(task_id + 1)  # noqa: UP032 — verbatim legacy formatting (R8)
    if self._TaskManager__is_multiagent():
        logger.info(
            f"Getting {current_task} from prompt responses of type {type(prompt_responses)}, prompt responses key {prompt_responses.keys()}"  # noqa: E501 — verbatim legacy log line (R8)
        )
        prompts: Any = prompt_responses.get(current_task, None)  # why: free-form payload; None crash surface preserved
        self.prompt_map = {}
        for agent in self.task_config["tools_config"]["llm_agent"]["llm_config"]["agent_map"]:
            prompt = prompts[agent]["system_prompt"]
            prompt = self._TaskManager__prefill_prompts(self.task_config, prompt, self.task_config["task_type"])
            prompt = self._TaskManager__get_final_prompt(prompt, current_date, current_time, self.timezone)
            if agent == self.task_config["tools_config"]["llm_agent"]["llm_config"]["default_agent"]:
                self.system_prompt = {"role": "system", "content": prompt}
            self.prompt_map[agent] = prompt
        logger.info(f"Initialised prompt dict {self.prompt_map}, Set default prompt {self.system_prompt}")
    else:
        # Missing task prompts (e.g. no conversation_details.json) must
        # degrade to an empty system prompt, not crash the call with
        # `argument of type 'NoneType' is not iterable` (observed live:
        # inbound AI call dropped right after WS accept).
        self.prompts = self._TaskManager__prefill_prompts(
            self.task_config, prompt_responses.get(current_task, None) or {}, self.task_config["task_type"]
        )

    if "system_prompt" in self.prompts:
        # This isn't a graph based agent
        enriched_prompt = self.prompts["system_prompt"]
        if self.context_data and self.context_data.get("recipient_data", {}).get("call_sid"):
            self.call_sid = self.context_data["recipient_data"]["call_sid"]

        enriched_prompt = structure_system_prompt(
            self.prompts["system_prompt"],
            self.run_id,
            self.assistant_id,
            self.call_sid,
            self.context_data,
            self.timezone,
            self.is_web_based_call,
        )

        notes = ""
        if self._is_conversation_task() and self.use_fillers:
            notes = "### Note:\n"
            notes += f"1.{FILLER_PROMPT}\n"

        final_prompt = f"\n## Agent Prompt:\n\n{enriched_prompt}\n{notes}\n\n## Transcript:\n"
        self.prompts["system_prompt"] = final_prompt

        self.system_prompt = {"role": "system", "content": final_prompt}
    else:
        self.system_prompt = {"role": "system", "content": ""}

    self.conversation_history.setup_system_prompt(self.system_prompt)

    self.multilingual_prompts = {}
    raw_multilingual = prompt_responses.get(current_task, {}).get("multilingual_prompts", {})
    if raw_multilingual and not self._TaskManager__is_multiagent():
        for lang_code, lang_prompt in raw_multilingual.items():
            enriched = structure_system_prompt(
                lang_prompt,
                self.run_id,
                self.assistant_id,
                self.call_sid,
                self.context_data,
                self.timezone,
                self.is_web_based_call,
            )
            notes = ""
            if self._is_conversation_task() and self.use_fillers:
                notes = "### Note:\n"
                notes += f"1.{FILLER_PROMPT}\n"
            self.multilingual_prompts[lang_code] = f"\n## Agent Prompt:\n\n{enriched}\n{notes}\n\n## Transcript:\n"
        logger.info(f"Loaded multilingual prompts for languages: {list(self.multilingual_prompts.keys())}")

    # Pin the STARTING language for LID-switch calls. Drift is not switch-only: QA showed
    # the main LLM opening partly in Hindi before any switch ever happened (7c7d4b00) and
    # closing in English after a clean all-Telugu run (a39f691c) — a switch-time-only note
    # cannot cover either. Multilingual path only; single-language agents are untouched.
    if self.language_switcher is not None and self.system_prompt.get("content"):
        self._TaskManager__apply_language_directive(self.language)

    # If using knowledge_agent, inject the prompt into agent config so agent can read it
    try:
        if self._TaskManager__is_knowledgebase_agent() and "llm_agent" in self.task_config["tools_config"]:
            if "llm_config" in self.task_config["tools_config"]["llm_agent"]:
                self.task_config["tools_config"]["llm_agent"]["llm_config"]["prompt"] = self.system_prompt["content"]
    except Exception as e:
        logger.error(f"Failed to inject prompt into knowledge agent config: {e}")


def prefill_prompts(self: PromptSession, task: Any, prompt: Any, task_type: Any) -> Any:
    """Default extraction/summarization prompts when the payload carries none.

    Verbatim Region E body. Also re-pins the session timezone from recipient data (the
    legacy double-read `load_prompt` performs on task 0 — preserved quirk).

    Args:
        self: The live call session (the legacy TaskManager injects itself).
        task: The task config dict (read for the extraction schema).
        prompt: The stored prompt payload for the task, possibly empty.
        task_type: The task's type string.

    Returns:
        The payload passed through, or a synthesized ``{"system_prompt": ...}`` for
        an empty extraction/summarization payload.
    """
    if (
        self.context_data
        and "recipient_data" in self.context_data
        and self.context_data["recipient_data"]
        and self.context_data["recipient_data"].get("timezone", None)
    ):
        self.timezone = pytz.timezone(self.context_data["recipient_data"]["timezone"])
    current_date, current_time = get_date_time_from_timezone(self.timezone)

    if not prompt and task_type in ("extraction", "summarization"):
        if task_type == "extraction":
            extraction_json = task.get("tools_config").get("llm_agent", {}).get("llm_config", {}).get("extraction_json")
            # Schema goes in as a .format() argument, so variables inside it reached the model as literal braces.
            if isinstance(extraction_json, str):
                extraction_json = update_prompt_with_context(extraction_json, self.context_data)
            prompt = EXTRACTION_PROMPT.format(current_date, current_time, self.timezone, extraction_json)
            return {"system_prompt": prompt}
        elif task_type == "summarization":
            return {"system_prompt": SUMMARIZATION_PROMPT}
    return prompt


def process_stop_words(self: PromptSession, text_chunk: str, meta_info: dict) -> str:
    """Trim a trailing "user:" stop-word from a small-model LLM chunk.

    Verbatim Region E body (the LLM output path dispatches here through the
    TaskManager delegator).

    Args:
        self: The live call session (unused by the body; the delegation contract).
        text_chunk: The LLM text chunk to trim.
        meta_info: The chunk's meta dict (read for ``end_of_llm_stream``).

    Returns:
        The chunk, with a trailing stop-word removed on the final stream chunk.
    """
    # THis is to remove stop words. Really helpful in smaller 7B models
    if "end_of_llm_stream" in meta_info and meta_info["end_of_llm_stream"] and "user" in text_chunk[-5:].lower():
        if text_chunk[-5:].lower() == "user:":
            text_chunk = text_chunk[:-5]
        elif text_chunk[-4:].lower() == "user":
            text_chunk = text_chunk[:-4]

    # index = text_chunk.find("AI")
    # if index != -1:
    #     text_chunk = text_chunk[index+2:]
    return text_chunk
