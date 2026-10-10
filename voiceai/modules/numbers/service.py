"""Phone number business logic (spec 0074).

All routing decisions live here (Rule 1e): the controller parses and returns,
this layer decides. The one rule worth stating plainly is that an unassigned DID
is indistinguishable from a missing one on the wire — Talko's inbound lookup is
fail-closed and must not leak which numbers exist.
"""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.modules.numbers import constants as C
from voiceai.modules.numbers.errors import NumberUnassignedError, PhoneNumberNotFoundError
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.repository import PhoneNumberRepository
from voiceai.modules.numbers.static_methods import resolve_payload

__all__ = ["PhoneNumbersService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class PhoneNumbersService:
    """Create, route and retire the tenant's phone numbers.

    Args:
        repository: Tenant-scoped storage for the numbers collection.
    """

    def __init__(self, repository: PhoneNumberRepository) -> None:
        self._repo = repository

    async def create(self, number: PhoneNumber) -> PhoneNumber:
        """Register a new number.

        Args:
            number: The number to register.

        Returns:
            The stored number.
        """
        created = await self._repo.create(number)
        logger.info(C.LOG_CREATED, created.id)
        return created

    async def list_all(self) -> list[PhoneNumber]:
        """Return every number this tenant owns."""
        return await self._repo.list_all()

    async def get(self, number_id: str) -> PhoneNumber:
        """Return one number, or raise when it does not exist.

        Args:
            number_id: Storage identifier.

        Returns:
            The stored number.

        Raises:
            PhoneNumberNotFoundError: No active number has this id in the tenant.
        """
        number = await self._repo.get(number_id)
        if number is None:
            raise PhoneNumberNotFoundError(number_id)
        return number

    async def assign(
        self,
        number_id: str,
        agent_id: str,
        talko_partner_id: int | None = None,
        talko_vendor_config_id: str | None = None,
    ) -> PhoneNumber:
        """Route this number's inbound calls to `agent_id`.

        The Talko owner fields are only overwritten when provided — an omitted
        value keeps what is already stored, which is what the legacy endpoint
        did and what single-entry onboarding depends on.

        Args:
            number_id: Storage identifier.
            agent_id: The agent inbound calls route to.
            talko_partner_id: Talko owner, when changing it.
            talko_vendor_config_id: Talko vendor config, when changing it.

        Returns:
            The updated number.

        Raises:
            PhoneNumberNotFoundError: No active number has this id in the tenant.
        """
        number = await self.get(number_id)
        number.assigned_agent_id = agent_id
        if talko_partner_id is not None:
            number.talko_partner_id = talko_partner_id
        if talko_vendor_config_id is not None:
            number.talko_vendor_config_id = talko_vendor_config_id
        saved = await self._repo.update(number)
        logger.info(C.LOG_ASSIGNED, number_id, agent_id)
        return saved

    async def unassign(self, number_id: str) -> PhoneNumber:
        """Stop routing this number; inbound to it is refused thereafter.

        Args:
            number_id: Storage identifier.

        Returns:
            The updated number.

        Raises:
            PhoneNumberNotFoundError: No active number has this id in the tenant.
        """
        number = await self.get(number_id)
        number.assigned_agent_id = None
        saved = await self._repo.update(number)
        logger.info(C.LOG_UNASSIGNED, number_id)
        return saved

    async def delete(self, number_id: str) -> None:
        """Tombstone a number so it stops resolving.

        Args:
            number_id: Storage identifier.

        Raises:
            PhoneNumberNotFoundError: No active number has this id in the tenant
                (including one already deleted — the delete is not idempotent
                because the caller asked to remove something that is not there).
        """
        if not await self._repo.delete(number_id):
            raise PhoneNumberNotFoundError(number_id)
        logger.info(C.LOG_DELETED, number_id)

    async def resolve(self, digits: str) -> dict[str, object]:
        """Resolve a dialled DID to its routing target (the Talko lookup).

        Args:
            digits: The DID as dialled, in any format.

        Returns:
            The resolve body: `agent_id`, plus the Talko owner keys when set.

        Raises:
            NumberUnassignedError: The DID is unknown or has no agent. Both cases
                raise the same error so the endpoint reveals nothing.
        """
        record = await self._repo.find_by_digits(digits)
        if record is None:
            logger.info(C.LOG_RESOLVE_MISS, "none")
            raise NumberUnassignedError(digits)
        if not record.assigned_agent_id:
            logger.info(C.LOG_RESOLVE_MISS, record.id or "none")
            raise NumberUnassignedError(record.id or digits)
        logger.info(C.LOG_RESOLVED, record.id, record.assigned_agent_id)
        return resolve_payload(record)
