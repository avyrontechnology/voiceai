"""Storage adapters for agent definitions and prompts (AGENTS.md rule 1d; spec 0002, A3).

Two port implementations live here — the only agents-module code that touches a store:

* ``RedisAgentRepository`` satisfies ``AgentDefinitionPort`` over the shared redis, keeping
  every legacy access pattern verbatim: bare-UUID keys, falsy-payload-means-absent reads,
  and the ``KEYS *`` + skip-``":"``-keys directory scan behind one documented method.
* ``FilePromptStore`` satisfies ``AgentSessionStorePort`` over the CWD-relative prompt-file
  directory, delegating THROUGH the live ``voiceai.helpers.utils`` attributes (see
  ``utils.py``) so legacy monkeypatch targets keep intercepting.

Clients arrive by constructor (rule 9); tests inject a dict-backed fake for redis.
"""

from __future__ import annotations

import json
from typing import Any, Final, Protocol, cast

from pydantic import ValidationError

from voiceai.common.logger import get_logger
from voiceai.modules.agents.constants import (
    AGENT_ID_KEY,
    MODULE_NAME,
    REDIS_KEY_NAMESPACE_SEPARATOR,
    REDIS_SCAN_ALL_PATTERN,
)
from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.static_methods import collect_agent_records
from voiceai.modules.agents.utils import read_conversation_details, write_conversation_details

__all__ = ["FilePromptStore", "RedisAgentRepository", "RedisLike"]

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

        # legacy-parity(spec-0002): the prompt file is deliberately NOT removed — the
        prompt-file-orphan-on-DELETE quirk is a behavior invariant until its own spec.

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
