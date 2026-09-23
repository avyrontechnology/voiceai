"""Storage adapters for agent definitions and prompts (AGENTS.md rule 1d; T3 greenfield).

Port implementations — the only agents-module code that touches a store:

* ``MongoAgentDefinitions`` satisfies ``AgentDefinitionPort`` over the indexed
  `agents` collection (Atlas in prod, in-memory in tests): no `KEYS *` scan, the
  directory pages through the collection and reuses the genuine-record filter.
* ``MongoAgentPrompts`` satisfies ``AgentSessionStorePort`` over the
  `agent_prompts` collection, replacing CWD-relative prompt files.
* ``RedisAgentRepository`` + ``FilePromptStore`` stay for quickstart (which pins
  them explicitly) until the T7 cutover retires them — new code must not use them.

Clients arrive by constructor (rule 9); tests inject in-memory repositories.
"""

from __future__ import annotations

import json
from typing import Any, Final, Protocol, cast

from pydantic import ValidationError

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.logger import get_logger
from voiceai.common.pagination import PaginationParams
from voiceai.database.base import BaseFields
from voiceai.database.repository import BaseRepository
from voiceai.modules.agents.constants import (
    AGENT_ID_KEY,
    MODULE_NAME,
    REDIS_KEY_NAMESPACE_SEPARATOR,
    REDIS_SCAN_ALL_PATTERN,
)
from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.models.definition import AgentDefinition
from voiceai.modules.agents.models.prompts import AgentPrompts
from voiceai.modules.agents.static_methods import collect_agent_records
from voiceai.modules.agents.utils import (
    delete_conversation_details,
    read_conversation_details,
    write_conversation_details,
)

__all__ = [
    "FilePromptStore",
    "MongoAgentDefinitions",
    "MongoAgentPrompts",
    "RedisAgentRepository",
    "RedisLike",
]

_LOGGER = get_logger(MODULE_NAME)

# Message and log text match the legacy quickstart scan's debug line word for word.
_LOG_UNREADABLE_KEY: Final[str] = "Skipping unreadable agent key %s: %s"
# Client-safe: names the failure class, never the pydantic error text (AGENTS.md §4).
_AGENT_CONFIG_INVALID_MESSAGE: Final[str] = "Stored agent configuration failed schema validation"


class RedisLike(Protocol):
    """The slice of the async redis surface this adapter consumes.

    Parameter names mirror ``redis.asyncio.Redis`` so the production client conforms
    structurally; tests conform with a dict-backed fake. Values are decoded strings
    (``decode_responses=True``), exactly as both the quickstart server and ``core.redis``
    configure the client.
    """

    async def get(self, name: str) -> str | None:
        """Return the string stored at ``name``, or ``None`` when the key is absent."""

    async def set(self, name: str, value: str) -> Any:  # why: redis-py answers a driver-specific ack
        """Store ``value`` at ``name``, overwriting any existing value."""

    async def exists(self, *names: str) -> int:
        """Count how many of ``names`` exist."""

    async def delete(self, *names: str) -> int:
        """Remove ``names``; answer how many were removed."""

    async def keys(self, pattern: str) -> list[str]:
        """Return every key matching ``pattern``."""


class RedisAgentRepository:
    """``AgentDefinitionPort`` adapter over the shared redis (bare-UUID key scheme).

    .. deprecated:: T3 keeps this for quickstart (which pins it explicitly) until
        the T7 cutover. New code uses ``MongoAgentDefinitions``.

    Args:
        redis_client: The injected redis-like client (rule 9). Production wiring (step A4)
            passes the async client from ``core.redis``; tests pass a dict-backed fake.
    """

    def __init__(self, redis_client: RedisLike) -> None:
        self._redis = redis_client

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Return the raw stored configuration for ``agent_id``, or ``None`` when absent.

        The raw dict is the census-verified engine seam: no validation, no reshaping.

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            The parsed configuration dict, or ``None`` when no record exists.

        Raises:
            json.JSONDecodeError: When the stored payload is not valid JSON —
                # legacy-parity(spec-0002): the quickstart read raises out of ``json.loads``
                and the controller's 404-swallowed-to-500 quirk depends on that.
        """
        raw = await self._redis.get(agent_id)
        # legacy-parity(spec-0002): every falsy payload ("" included) reads as absent —
        # the quickstart guard is `if not agent_data`, not an existence check.
        if not raw:
            return None
        return cast("dict[str, Any]", json.loads(raw))  # why: raw config dicts are the engine seam

    async def get_agent_model(self, agent_id: str) -> AgentModel | None:
        """Return the validated schema view of a stored definition, or ``None`` when absent.

        The dual-return contract of spec 0002: ``get_agent`` answers the raw dict
        byte-identical for the engine/wire seam, while this method layers the authoring
        schema on top for callers that want validated fields (service, step A4).

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            The validated ``AgentModel``, or ``None`` when no record exists.

        Raises:
            AgentConfigInvalidError: When a stored record does not satisfy the schema.
        """
        config = await self.get_agent(agent_id)
        if config is None:
            return None
        try:
            return AgentModel.model_validate(config)
        except ValidationError as exc:
            raise AgentConfigInvalidError(
                _AGENT_CONFIG_INVALID_MESSAGE, details={AGENT_ID_KEY: agent_id}, cause=exc
            ) from exc

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Store ``config`` under ``agent_id``, overwriting any existing definition.

        The value written is ``json.dumps(config)`` — the exact quickstart serialization.

        Args:
            agent_id: The bare-UUID agent id.
            config: The raw configuration dict (already schema-shaped by the caller).
        """
        await self._redis.set(agent_id, json.dumps(config))

    async def delete_agent(self, agent_id: str) -> bool:
        """Remove the definition for ``agent_id``; answer whether a record existed.

        Never touches the prompt store (single responsibility): the service deletes
        prompts prompts-first around this call, which is what retired the legacy
        orphan-on-DELETE quirk in T3.

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            ``True`` when a record existed and was deleted, ``False`` otherwise.
        """
        exists = await self._redis.exists(agent_id)
        if not exists:
            return False
        await self._redis.delete(agent_id)
        return True

    async def list_agents(self) -> list[dict[str, Any]]:
        """Return every genuine agent record as ``{"agent_id", "data"}`` dicts.

        The legacy directory scan, verbatim, behind this one documented method:
        ``KEYS *`` first, then ``":"``-namespaced keys skipped BEFORE the ``GET`` (reading
        a namespaced index set as a string raises WRONGTYPE), then per-key read failures
        logged at debug and skipped so one bad key never empties the directory.
        # TODO(spec-0002): ``KEYS *`` is documented preserved debt for the platform-store
        # spec (see ``constants.py``); replacing it with SCAN is that spec's change.

        Returns:
            The accepted records in scan order, IDs aligned per record.
        """
        keys = await self._redis.keys(REDIS_SCAN_ALL_PATTERN)
        if not keys:
            return []
        pairs: list[tuple[str, str | None]] = []
        for key in keys:
            if REDIS_KEY_NAMESPACE_SEPARATOR in key:
                continue
            try:
                pairs.append((key, await self._redis.get(key)))
            except Exception as exc:  # noqa: S112 - legacy-parity(spec-0002): log-and-skip, scan survives
                _LOGGER.debug(_LOG_UNREADABLE_KEY, key, exc)
        return collect_agent_records(pairs)


class FilePromptStore:
    """``AgentSessionStorePort`` adapter over the prompt-file directory.

    .. deprecated:: T3 keeps this for quickstart (which pins it explicitly) until
        the T7 cutover. New code uses ``MongoAgentPrompts``.

    Stateless on purpose: the storage root is the legacy module's live ``PREPROCESS_DIR``
    attribute (CWD-relative — see ``constants.py``), read at call time through
    ``voiceai.helpers.utils`` so existing monkeypatch targets keep intercepting.
    """

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Return the stored prompts for ``agent_id``, or ``None`` when none were saved.

        A missing or unreadable file is not an error: it degrades to ``None`` exactly as
        the legacy loader does mid-call.

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            The stored payload, or ``None``.
        """
        return await read_conversation_details(agent_id)

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Persist ``prompts`` for ``agent_id``; ``None`` stores JSON ``null`` (parity).

        Args:
            agent_id: The bare-UUID agent id.
            prompts: The payload to store, passed through opaque.
        """
        await write_conversation_details(agent_id, prompts)

    async def delete_prompts(self, agent_id: str) -> bool:
        """Remove the prompt file for ``agent_id``; ``True`` when one existed.

        Delegates through the agents-side seam (`utils.delete_conversation_details`),
        which mirrors the loader's path without a static legacy import.

        Args:
            agent_id: The bare-UUID agent id.

        Returns:
            ``True`` when a file was removed.
        """
        return await delete_conversation_details(agent_id)


def _pin(model: BaseFields, natural_id: str) -> BaseFields:
    """Pin a model's storage id to its natural key, returning it for chaining."""
    model.id = natural_id
    return model


class MongoAgentDefinitions:
    """``AgentDefinitionPort`` over the indexed `agents` collection (T3 greenfield).

    No `KEYS *` scan: the directory pages the collection and reuses the
    genuine-record filter over each config's JSON rendering, so the `/all`
    acceptance rules (tasks-list discriminator, per-key skip) hold verbatim.
    Deletes are soft (rule 5); reads skip inactive rows, so the observable
    contract matches the legacy destroy.

    Args:
        definitions: The `agents` collection repository.
    """

    def __init__(self, definitions: BaseRepository[AgentDefinition]) -> None:
        self._definitions = definitions

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Return the raw stored configuration for ``agent_id``, or ``None``."""
        stored = await self._definitions.get(agent_id)
        return dict(stored.config) if stored is not None else None

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Store ``config`` under ``agent_id``, overwriting any existing definition."""
        record = AgentDefinition(agent_id=agent_id, config=dict(config))
        _pin(record, agent_id)
        await self._definitions.insert(record)

    async def delete_agent(self, agent_id: str) -> bool:
        """Soft-delete the definition; ``True`` when a record was active."""
        return await self._definitions.soft_delete(agent_id)

    async def list_agents(self) -> list[dict[str, Any]]:
        """Return every genuine agent record as ``{"agent_id", "data"}`` dicts."""
        pairs: list[tuple[str, str | None]] = []
        page_number = 1
        while True:
            page = await self._definitions.list(PaginationParams(page=page_number, page_size=MAX_PAGE_SIZE))
            pairs.extend((record.agent_id, json.dumps(record.config)) for record in page.items)
            if not page.has_next:
                break
            page_number += 1
        return collect_agent_records(pairs)


class MongoAgentPrompts:
    """``AgentSessionStorePort`` over the `agent_prompts` collection (T3 greenfield).

    Replaces CWD-relative prompt files: payloads ride opaque (including the
    multiagent nesting), `None` stores JSON null exactly like the files did, and
    a missing document reads as `None`.

    Args:
        prompts: The `agent_prompts` collection repository.
    """

    def __init__(self, prompts: BaseRepository[AgentPrompts]) -> None:
        self._prompts = prompts

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Return the stored payload for ``agent_id``, or ``None`` when absent."""
        stored = await self._prompts.get(agent_id)
        return dict(stored.payload) if stored is not None and stored.payload is not None else None

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Persist ``prompts`` for ``agent_id``; ``None`` stores JSON null."""
        record = AgentPrompts(agent_id=agent_id, payload=dict(prompts) if prompts is not None else None)
        _pin(record, agent_id)
        await self._prompts.insert(record)

    async def delete_prompts(self, agent_id: str) -> bool:
        """Remove the payload for ``agent_id``; ``True`` when one was active."""
        return await self._prompts.soft_delete(agent_id)
