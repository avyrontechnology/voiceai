"""HTTP request/response schemas for the numbers module (spec 0074).

These mirror the legacy `platform` router bodies exactly: the module migration
must not change the wire contract, so field names, defaults and optionality are
carried over as they were.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.numbers.constants import (
    DEFAULT_COUNTRY,
    DEFAULT_PROVIDER,
    DEFAULT_STATUS,
    PhoneNumberProvider,
)

__all__ = [
    "AssignNumberRequest",
    "CreateNumberRequest",
    "NumberListResponse",
    "PhoneNumberResponse",
]


class CreateNumberRequest(BaseModel):
    """Body for registering a new number.

    Attributes:
        number: The DID to register, in any format.
        provider: Carrier it is provisioned with.
        country: Country it belongs to.
        talko_partner_id: Talko owner, letting Otoba be the single entry point.
        talko_vendor_config_id: Talko vendor config that dials the number.
    """

    number: str = Field(..., min_length=1)
    provider: PhoneNumberProvider = DEFAULT_PROVIDER
    country: str = Field(DEFAULT_COUNTRY, min_length=1)
    talko_partner_id: int | None = None
    talko_vendor_config_id: str | None = None


class AssignNumberRequest(BaseModel):
    """Body for routing a number to an agent.

    An omitted Talko field keeps the stored value (single-entry onboarding), so
    both are optional.

    Attributes:
        agent_id: The agent inbound calls route to.
        talko_partner_id: Talko owner, when changing it.
        talko_vendor_config_id: Talko vendor config, when changing it.
    """

    agent_id: str = Field(..., min_length=1)
    talko_partner_id: int | None = None
    talko_vendor_config_id: str | None = None


class PhoneNumberResponse(BaseModel):
    """A number as the API returns it.

    `number_id` is the legacy field name; it is the document's `id`, kept so
    existing clients keep working.

    Attributes:
        number_id: Storage identifier.
        number: The DID as entered.
        provider: Carrier the number is provisioned with.
        country: Country the number belongs to.
        assigned_agent_id: The routing target, or `None` when unassigned.
        status: Lifecycle status.
        talko_partner_id: Talko owner, when set.
        talko_vendor_config_id: Talko vendor config, when set.
        created_at: When the number was registered.
    """

    number_id: str
    number: str
    provider: PhoneNumberProvider
    country: str
    assigned_agent_id: str | None = None
    status: str = DEFAULT_STATUS
    talko_partner_id: int | None = None
    talko_vendor_config_id: str | None = None
    created_at: Any

    @classmethod
    def from_model(cls, record: Any) -> PhoneNumberResponse:
        """Build the response body from a stored number.

        Args:
            record: The stored `PhoneNumber`.

        Returns:
            The API-shaped response.
        """
        return cls(
            number_id=record.id or "",
            number=record.number,
            provider=record.provider,
            country=record.country,
            assigned_agent_id=record.assigned_agent_id,
            status=record.status,
            talko_partner_id=record.talko_partner_id,
            talko_vendor_config_id=record.talko_vendor_config_id,
            created_at=record.created_at,
        )


class NumberListResponse(BaseModel):
    """Every number the tenant owns.

    Attributes:
        numbers: The stored numbers.
    """

    numbers: list[PhoneNumberResponse]
