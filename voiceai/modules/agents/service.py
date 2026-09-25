"""Business logic of the agents module (AGENTS.md rule 1e; spec 0002, A4).

`AgentService` owns the CRUD-and-prompts flows the quickstart server serves today, behavior
preserved verbatim: `assistant_status` "seeding"/"updated" injection, extraction_json
generation (now behind `LlmPort`) and the falsy-prompts-to-`None` normalization. T3 ends
the prompt-file orphan on DELETE: prompts die prompts-first, so no delete strands a
payload. No HTTP types appear in any signature (rule 1e); the controller translates
module errors into statuses.

The definition store may be absent: when the container builds no repository, every
method raises `DependencyUnavailableError` (503) instead of crashing on a missing
client (defense in depth — production always wires the Mongo store since T3).
"""

from __future__ import annotations

from asyncio import Semaphore, gather
from collections.abc import Callable
from logging import Logger
from typing import Any, Final
from uuid import uuid4

from voiceai.common.errors import DependencyUnavailableError
from voiceai.modules.agents.constants import (
    AGENT_ID_KEY,
    AGENT_NOT_FOUND_MESSAGE,
    AGENT_STATE_CREATED,
    AGENT_STATE_DELETED,
    AGENT_STATE_UPDATED,
    AGENTS_KEY,
    ASSISTANT_STATUS_KEY,
    ASSISTANT_STATUS_SEEDING,
    ASSISTANT_STATUS_UPDATED,
    MAX_EXTRACTION_CONCURRENCY,
    STATE_KEY,
    TASK_TYPE_EXTRACTION,
    TASKS_KEY,
    WRITABLE_CHANNELS,
)
from voiceai.modules.agents.errors import AgentConfigInvalidError, AgentNotFoundError, PromptStoreError
from voiceai.modules.agents.exceptions import ensure_agent_exists
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort, LlmPort
from voiceai.modules.agents.static_methods import audit_provider_config

__all__ = ["AgentService"]

# Client-visible: `DependencyUnavailableError` echoes its message (it is not an internal error).
_DEFINITION_STORE_UNAVAILABLE_MESSAGE: Final[str] = "Agent definition store is not configured"

# Keys of the quickstart task/config dicts the extraction flow walks (wire contract, verbatim).
_TASK_TYPE_KEY: Final[str] = "task_type"
_TOOLS_CONFIG_KEY: Final[str] = "tools_config"
_LLM_AGENT_KEY: Final[str] = "llm_agent"
_EXTRACTION_DETAILS_KEY: Final[str] = "extraction_details"
_EXTRACTION_JSON_KEY: Final[str] = "extraction_json"
_MISSING_EXTRACTION_DETAILS_DEFAULT: Final[str] = ""

# The prompts response key of GET /agent/{agent_id}/prompts (quickstart wire shape).
_AGENT_PROMPTS_KEY: Final[str] = "agent_prompts"

# Chat-message keys for the extraction-generation call (quickstart lines 166-174, verbatim).
_ROLE_KEY: Final[str] = "role"
_CONTENT_KEY: Final[str] = "content"
_SYSTEM_ROLE: Final[str] = "system"
_USER_ROLE: Final[str] = "user"

# Identifiers only at INFO — never the config payload the quickstart used to log (AGENTS.md §4).
_LOG_CREATING_AGENT: Final[str] = "creating agent %s"
_LOG_UPDATING_AGENT: Final[str] = "updating agent %s"
# legacy-parity(spec-0002): the quickstart extraction-setup log line, word for word (no PII).
_LOG_EXTRACTION_SETUP: Final[str] = "Setting up follow up tasks"


class AgentService:
    """The agent-definition CRUD and prompt flows, quickstart-behavior-preserved (spec 0002).

    Every collaborator arrives by constructor (rule 9); tests inject in-memory fakes through
    the same signatures the production `register` wiring uses.

    Args:
        definitions: The definition store, or `None` when redis is unconfigured — every
            method then raises `DependencyUnavailableError` instead of touching a client.
        prompt_store: The per-agent prompt payload store (`conversation_details.json`).
        extraction_llm: The one-shot completion used to seed `extraction_json`.
        require_extraction_model: The legacy UPDATE-only guard on the extraction model env
            var; raises when the model is not configured (see `adapters/llm.py`).
        extraction_system_prompt: The system prompt of the extraction-generation call.
        logger: The `otobaai` module logger (rule 3), injected so tests can capture it.
        catalog: The provider catalog service (spec 0022, slice 2), or `None`
            for compositions without validation — unwired compositions skip
            provider checks with a warning, never silently (the container
            always wires it).
    """

    def __init__(
        self,
        definitions: AgentDefinitionPort | None,
        prompt_store: AgentSessionStorePort,
        extraction_llm: LlmPort,
        require_extraction_model: Callable[[], None],
        extraction_system_prompt: str,
        logger: Logger,
        catalog: Any = None,  # why: CatalogService; None keeps test compositions validation-free
    ) -> None:
        self._definitions = definitions
        self._prompt_store = prompt_store
        self._extraction_llm = extraction_llm
        self._require_extraction_model = require_extraction_model
        self._extraction_system_prompt = extraction_system_prompt
        self._logger = logger
        self._catalog = catalog

    async def _validate_providers(self, data: dict[str, Any]) -> None:
        """Reject provider/model/language values the catalog cannot resolve.

        Runs on the dumped config (schema defaults materialized) BEFORE any
        extraction LLM spend, so a typo fails fast and free (spec 0022).

        Args:
            data: The agent definition dump about to persist.

        Raises:
            AgentConfigInvalidError: With every problem and the valid values.
        """
        if self._catalog is None:
            self._logger.warning("agent provider validation skipped: catalog unwired")
            return
        entries = [entry.model_dump() for entry in await self._catalog.entries()]
        if not entries:
            # Pre-seed window: an empty catalog has nothing to validate against.
            # Warn loudly and skip (same posture as unwired) — lifespans seed
            # at boot, so this only fires before the first sync lands.
            self._logger.warning("agent provider validation skipped: catalog empty")
            return
        problems = audit_provider_config(data, entries, self._catalog.is_valid_language)
        if problems:
            raise AgentConfigInvalidError(
                "; ".join(problems),
                details={"problems": problems},
            )
        self._ensure_writable_channels(data.get("channels", []))

    @staticmethod
    def _ensure_writable_channels(channels: object) -> None:
        """Reject non-voice channels until their runtime lands (spec 0028).

        The schema accepts the full channel vocabulary so stored rows stay
        forward-compatible; the service allowlists what Phase A can actually
        run. Chat rejects loudly (no dormant data) until Phase C.

        Args:
            channels: The dumped `channels` value.

        Raises:
            AgentConfigInvalidError: Naming the rejected channels + valid set.
        """
        names = [str(channel) for channel in channels] if isinstance(channels, list) else []
        rejected = [channel for channel in names if channel not in WRITABLE_CHANNELS]
        if rejected:
            raise AgentConfigInvalidError(
                f"Channels not servable yet: {', '.join(rejected)} "
                f"(valid: {', '.join(sorted(WRITABLE_CHANNELS))}; chat arrives in Phase C).",
                details={"channels": rejected, "valid": sorted(WRITABLE_CHANNELS)},
            )

    def _require_definitions(self) -> AgentDefinitionPort:
        """Return the definition store, or raise because none was injected.

        Returns:
            The injected `AgentDefinitionPort`.

        Raises:
            DependencyUnavailableError: When no store exists (the container always
                wires one since T3; `None` only arrives from hand-built services).
        """
        if self._definitions is None:
            raise DependencyUnavailableError(_DEFINITION_STORE_UNAVAILABLE_MESSAGE)
        return self._definitions

    async def _generate_extraction_json(self, extraction_details: str) -> str:
        """Generate the extraction JSON text for one task through the injected LLM port.

        The message shape is the quickstart call verbatim: system prompt first, the task's
        `extraction_details` as the user turn.

        Args:
            extraction_details: The task's extraction brief, passed through opaque.

        Returns:
            The generated text, stored under `extraction_json` untouched.
        """
        messages: list[dict[str, Any]] = [  # why: chat messages are the LLM wire seam
            {_ROLE_KEY: _SYSTEM_ROLE, _CONTENT_KEY: self._extraction_system_prompt},
            {_ROLE_KEY: _USER_ROLE, _CONTENT_KEY: extraction_details},
        ]
        return await self._extraction_llm(messages)

    async def _generate_extraction_batch(self, jobs: list[tuple[int, str]]) -> list[tuple[int, str]]:
        """Generate extraction prompts concurrently, order-preserving (spec 0012).

        The collect-then-generate split keeps the legacy walk semantics (malformed
        tasks raise during collection, guards fire in walk order) while N independent
        generations overlap: wall-time drops from the sum to the slowest single task,
        bounded by ``MAX_EXTRACTION_CONCURRENCY`` so a 50-task agent cannot stampede
        the provider.

        Args:
            jobs: ``(task index, extraction details)`` pairs in walk order.

        Returns:
            ``(task index, generated text)`` pairs in the same order (`gather`
            preserves input order, so assignment back by index is exact).
        """
        if not jobs:
            return []
        semaphore = Semaphore(MAX_EXTRACTION_CONCURRENCY)

        async def _one(details: str) -> str:
            async with semaphore:
                return await self._generate_extraction_json(details)

        generated = await gather(*(_one(details) for _, details in jobs))
        return [(index, text) for (index, _), text in zip(jobs, generated, strict=True)]

    async def _agent_record_exists(self, store: AgentDefinitionPort, agent_id: str) -> bool:
        """Report whether any record — even an unparseable one — exists for `agent_id`.

        # legacy-parity(spec-0002): the quickstart prompts route checks existence with a
        bare falsy read and never parses the payload, so a corrupt stored record still
        counts as existing and still serves its prompts.

        Args:
            store: The definition store to ask.
            agent_id: The bare-UUID agent id.

        Returns:
            `True` when a non-empty record is stored, parseable or not.
        """
        try:
            return await store.get_agent(agent_id) is not None
        except ValueError:  # json.JSONDecodeError — the record exists, it just will not parse
            return True

    async def get_agent(self, agent_id: str) -> dict[str, Any]:  # why: raw config dicts are the engine seam
        """Return the raw stored configuration for `agent_id`.

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            The stored configuration dict, byte-identical to what the quickstart serves.

        Raises:
            AgentNotFoundError: When no record exists (the controller swallows this into
                the legacy 500 on `/agent/{agent_id}` — see `controller.py`).
            DependencyUnavailableError: When the definition store is unconfigured.
        """
        store = self._require_definitions()
        return ensure_agent_exists(await store.get_agent(agent_id), agent_id)

    async def get_agent_prompts(self, agent_id: str) -> dict[str, Any]:  # why: quickstart wire shape is a raw dict
        """Return the quickstart prompts payload: `{"agent_id", "agent_prompts"}`.

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            The wire dict; `agent_prompts` is the stored payload, or `None` for ANY falsy
            read (# legacy-parity(spec-0002): the quickstart guard is `if not prompts`).

        Raises:
            AgentNotFoundError: When no agent record exists (this route answers a true 404).
            DependencyUnavailableError: When the definition store is unconfigured.
        """
        store = self._require_definitions()
        if not await self._agent_record_exists(store, agent_id):
            raise AgentNotFoundError(AGENT_NOT_FOUND_MESSAGE, details={AGENT_ID_KEY: agent_id})
        prompts = await self._prompt_store.get_prompts(agent_id)
        if not prompts:
            prompts = None  # legacy-parity(spec-0002): falsy payloads ({} included) answer null
        return {AGENT_ID_KEY: agent_id, _AGENT_PROMPTS_KEY: prompts}

    async def create_agent(
        self,
        config: AgentModel,
        prompts: dict[str, Any] | None,  # why: prompt payloads are free-form JSON, stored opaque
    ) -> dict[str, Any]:  # why: quickstart wire shape is a raw dict
        """Create an agent: mint the id, seed the status, generate extraction prompts, persist.

        Quickstart create verbatim: `assistant_status="seeding"`, extraction tasks walked by
        direct subscript (a malformed task raises), NO guard on the extraction model env var,
        and the config write and the prompt-file write gathered concurrently.

        Args:
            config: The validated agent definition (the caller's payload schema).
            prompts: The optional prompt payload, stored opaque (`None` stores JSON null).

        Returns:
            `{"agent_id": <new uuid>, "state": "created"}`.

        Raises:
            DependencyUnavailableError: When the definition store is unconfigured.
        """
        store = self._require_definitions()
        agent_uuid = str(uuid4())
        data_for_db = config.model_dump()
        data_for_db[ASSISTANT_STATUS_KEY] = ASSISTANT_STATUS_SEEDING  # legacy-parity(spec-0002)
        await self._validate_providers(data_for_db)
        self._logger.info(_LOG_CREATING_AGENT, agent_uuid)
        if len(data_for_db[TASKS_KEY]) > 0:
            self._logger.info(_LOG_EXTRACTION_SETUP)
            jobs: list[tuple[int, str]] = []
            for index, task in enumerate(data_for_db[TASKS_KEY]):
                if task[_TASK_TYPE_KEY] == TASK_TYPE_EXTRACTION:
                    # legacy-parity(spec-0002): create does NOT call the env-var guard and
                    # subscripts extraction_details directly, exactly as quickstart does.
                    details = data_for_db[TASKS_KEY][index][_TOOLS_CONFIG_KEY][_LLM_AGENT_KEY][_EXTRACTION_DETAILS_KEY]
                    jobs.append((index, details))
            for index, extraction_prompt in await self._generate_extraction_batch(jobs):
                data_for_db[TASKS_KEY][index][_TOOLS_CONFIG_KEY][_LLM_AGENT_KEY][_EXTRACTION_JSON_KEY] = (
                    extraction_prompt
                )
        await gather(
            store.save_agent(agent_uuid, data_for_db),
            self._prompt_store.save_prompts(agent_uuid, prompts),
        )
        return {AGENT_ID_KEY: agent_uuid, STATE_KEY: AGENT_STATE_CREATED}

    async def update_agent(
        self,
        agent_id: str,
        config: AgentModel,
        prompts: dict[str, Any] | None,  # why: prompt payloads are free-form JSON, stored opaque
    ) -> dict[str, Any]:  # why: quickstart wire shape is a raw dict
        """Overwrite an agent's configuration, quickstart update-path verbatim.

        `assistant_status="updated"`, the stored record parsed first (a corrupt payload
        raises exactly as the legacy `json.loads` did), extraction tasks walked with `.get`
        defaults, and the env-var guard invoked BEFORE any generation — the UPDATE-only
        asymmetry (# legacy-parity(spec-0002)).

        Args:
            agent_id: The bare-UUID agent id being updated.
            config: The validated replacement definition.
            prompts: The optional prompt payload, stored opaque.

        Returns:
            `{"agent_id": agent_id, "state": "updated"}`.

        Raises:
            AgentNotFoundError: When no record exists (the controller swallows this into
                the legacy 500).
            DependencyUnavailableError: When the definition store is unconfigured.
        """
        store = self._require_definitions()
        ensure_agent_exists(await store.get_agent(agent_id), agent_id)
        new_data = config.model_dump()
        new_data[ASSISTANT_STATUS_KEY] = ASSISTANT_STATUS_UPDATED  # legacy-parity(spec-0002)
        await self._validate_providers(new_data)
        self._logger.info(_LOG_UPDATING_AGENT, agent_id)
        jobs: list[tuple[int, str]] = []
        for index, task in enumerate(new_data.get(TASKS_KEY, [])):
            if task.get(_TASK_TYPE_KEY) == TASK_TYPE_EXTRACTION:
                self._require_extraction_model()  # legacy-parity(spec-0002): UPDATE-only guard
                details = task[_TOOLS_CONFIG_KEY][_LLM_AGENT_KEY].get(
                    _EXTRACTION_DETAILS_KEY, _MISSING_EXTRACTION_DETAILS_DEFAULT
                )
                jobs.append((index, details))
        for index, extraction_prompt in await self._generate_extraction_batch(jobs):
            new_data[TASKS_KEY][index][_TOOLS_CONFIG_KEY][_LLM_AGENT_KEY][_EXTRACTION_JSON_KEY] = extraction_prompt
        await gather(
            store.save_agent(agent_id, new_data),
            self._prompt_store.save_prompts(agent_id, prompts),
        )
        return {AGENT_ID_KEY: agent_id, STATE_KEY: AGENT_STATE_UPDATED}

    async def delete_agent(self, agent_id: str) -> dict[str, Any]:  # why: quickstart wire shape is a raw dict
        """Delete an agent's definition together with its prompt payload.

        Prompts-first ordering (T3): the payload dies before the definition, so a
        failed definition delete leaves a retryable state instead of an orphan —
        the legacy orphan-on-DELETE quirk is retired. A prompts-store failure is
        logged and still aborts before the definition is touched.

        Args:
            agent_id: The bare-UUID agent id to delete.

        Returns:
            `{"agent_id": agent_id, "state": "deleted"}`.

        Raises:
            AgentNotFoundError: When no record exists (the controller swallows this into
                the legacy 500).
            DependencyUnavailableError: When the definition store is unconfigured.
        """
        store = self._require_definitions()
        try:
            await self._prompt_store.delete_prompts(agent_id)
        except Exception as exc:
            raise PromptStoreError("Prompt payload deletion failed", cause=exc) from exc
        if not await store.delete_agent(agent_id):
            raise AgentNotFoundError(AGENT_NOT_FOUND_MESSAGE, details={AGENT_ID_KEY: agent_id})
        return {AGENT_ID_KEY: agent_id, STATE_KEY: AGENT_STATE_DELETED}

    async def list_agents(self) -> dict[str, Any]:  # why: quickstart wire shape is a raw dict
        """Return the quickstart directory payload: `{"agents": [{"agent_id", "data"}, ...]}`.

        The scan quirks (`KEYS *`, `":"`-keys skipped, per-key failures logged and skipped)
        live behind the repository's one documented method.

        Returns:
            The wire dict, `agents` empty when nothing genuine is stored.

        Raises:
            DependencyUnavailableError: When the definition store is unconfigured.
        """
        store = self._require_definitions()
        return {AGENTS_KEY: await store.list_agents()}
