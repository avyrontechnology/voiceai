"""Voice library business logic: tenant-scoped CRUD with ownership (spec 0025).

All business logic, no HTTP types. Agent attach is ownership-checked through
the scoped definitions port (unknown-or-foreign agent reads as missing — the
place-call precedent); provider names resolve in the catalog (voice names are
user data and stay free).
"""

from __future__ import annotations

import logging
from typing import Any

from voiceai.common.errors import NotFoundError
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.voices import constants as C
from voiceai.modules.voices.errors import InvalidVoiceError
from voiceai.modules.voices.exceptions import ensure_voice_found
from voiceai.modules.voices.models import VoiceRecord, VoiceSource
from voiceai.modules.voices.repository import VoicesRepository
from voiceai.modules.voices.utils import ensure_agent_visible

__all__ = ["VoicesService"]

logger: logging.Logger = get_logger("voices")


class VoicesService:
    """Serve the per-agent custom voice library.

    Args:
        repository: The `voices` collection (tenant-scoped view).
        definitions: The agents definition port for attach ownership, or
            `None` — unwired compositions skip the agent check with a warning.
        catalog: The provider catalog service for provider validation, or
            `None` — unwired compositions skip with a warning (the container
            always wires both).
    """

    def __init__(
        self,
        repository: VoicesRepository,
        definitions: Any = None,  # why: AgentDefinitionPort; None keeps test compositions check-free
        catalog: Any = None,  # why: CatalogService; None keeps test compositions validation-free
    ) -> None:
        self._repository = repository
        self._definitions = definitions
        self._catalog = catalog

    async def _check_agent(self, agent_id: str | None) -> None:
        """Reject attaches to unknown-or-foreign agents (no oracle)."""
        if agent_id is None:
            return
        if self._definitions is None:
            logger.warning("voice agent check skipped: definitions unwired")
            return
        await ensure_agent_visible(self._definitions, agent_id)

    async def _check_provider(self, provider: str, language: str | None) -> None:
        """Reject unresolvable providers and malformed languages."""
        if self._catalog is None:
            logger.warning("voice provider validation skipped: catalog unwired")
            return
        providers = {row.provider for row in await self._catalog.entries()}
        if provider not in providers:
            raise InvalidVoiceError(
                f"Unknown voice provider {provider!r}.",
                details={"provider": provider, "valid": sorted(providers)},
            )
        if language is not None and not self._catalog.is_valid_language(language):
            raise InvalidVoiceError(
                f"Malformed language code {language!r}.",
                details={"language": language},
            )

    async def create_voice(
        self,
        *,
        agent_id: str | None,
        name: str,
        provider: str,
        provider_voice_id: str,
        source: VoiceSource = "provider",
        language: str | None = None,
    ) -> VoiceRecord:
        """Store one custom voice (legacy `create_voice` semantics).

        Args:
            agent_id: Owning agent (ownership-checked) or `None` (library).
            name: Tenant-given label.
            provider: Registry provider key (catalog-validated).
            provider_voice_id: Provider-side identifier.
            source: How the voice entered the library.
            language: BCP-47 tag when given.

        Returns:
            The persisted row.

        Raises:
            VoiceNotFoundError: When `agent_id` names no visible agent.
            InvalidVoiceError: On unknown provider or malformed language.
        """
        await self._check_agent(agent_id)
        await self._check_provider(provider, language)
        voice = VoiceRecord(
            voice_id=new_id(C.VOICE_ID_PREFIX),
            agent_id=agent_id,
            name=name,
            provider=provider,
            provider_voice_id=provider_voice_id,
            source=source,
            language=language,
        )
        return await self._repository.save_voice(voice)

    async def list_voices(self, agent_id: str | None = None) -> list[VoiceRecord]:
        """List this tenant's voices, optionally for one agent."""
        return list(await self._repository.list_voices(agent_id))

    async def delete_voice(self, voice_id: str) -> None:
        """Delete one voice; foreign ids read as missing (no oracle).

        Raises:
            VoiceNotFoundError: When the row is missing or foreign (the scoped
                store's generic not-found converts here — one module error).
        """
        try:
            deleted = await self._repository.delete_voice(voice_id)
        except NotFoundError:
            deleted = False
        if not deleted:
            ensure_voice_found(None, voice_id)
