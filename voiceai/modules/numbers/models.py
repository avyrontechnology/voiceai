"""Phone number models (spec 0074).

`PhoneNumber` is a `BaseFields` document: inheriting it is what replaces the
legacy `PlatformRow(payload: dict)` envelope, so the fields below are actually
persisted instead of hidden inside an opaque dict.
"""

from __future__ import annotations

from pydantic import Field

from voiceai.database.base import BaseFields
from voiceai.modules.numbers.constants import (
    DEFAULT_COUNTRY,
    DEFAULT_PROVIDER,
    DEFAULT_STATUS,
    PhoneNumberProvider,
)

__all__ = ["PhoneNumber", "PhoneNumberProvider"]


class PhoneNumber(BaseFields):
    """One dialled-in number (DID) owned by this tenant (spec 0074).

    The DID is stored as entered (`+9179…`, `9179…`, `91 79-…`): inbound lookup
    normalizes rather than rejecting, because the carrier hands us whatever the
    customer dialled and Talko must resolve the same variants we do.

    Attributes:
        number: The DID as entered.
        provider: Carrier the number is provisioned with.
        country: Country the number belongs to.
        assigned_agent_id: The agent inbound calls route to; `None` = unassigned,
            which resolve treats as a fail-closed miss.
        status: Lifecycle status of the number.
        talko_partner_id: Talko owner id, letting Otoba be the single entry point —
            on a DB-miss Talko uses this instead of holding its own DID row.
        talko_vendor_config_id: Talko vendor config that dials the number.
    """

    number: str = Field(..., min_length=1)
    provider: PhoneNumberProvider = DEFAULT_PROVIDER
    country: str = DEFAULT_COUNTRY
    assigned_agent_id: str | None = None
    status: str = DEFAULT_STATUS
    talko_partner_id: int | None = None
    talko_vendor_config_id: str | None = None
