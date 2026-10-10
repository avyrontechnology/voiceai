"""Every literal the numbers module uses: identity, routes, defaults, and log templates."""

from __future__ import annotations

from typing import Final, Literal

from voiceai.database.constants import Collections

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "numbers"
NUMBERS_ROUTE_PREFIX: Final[str] = "/phone-numbers"
NUMBERS_TAG: Final[str] = "Phone Numbers"

# --- Persistence -----------------------------------------------------------------------
#: Collection holding the numbers, from the single registry (Rule 5). The member
#: keeps its `PLATFORM_` prefix because the row name is unchanged since before
#: the module owned the family — renaming it would orphan live rows (spec 0074).
PHONE_NUMBERS_COLLECTION: Final[Collections] = Collections.PLATFORM_NUMBERS

#: Document field the Talko resolve lookup filters/normalizes against.
DIGITS_FIELD: Final[str] = "number"

# --- Defaults -------------------------------------------------------------------------
#: The carriers a DID can be provisioned with. Closed vocabulary so a typo is a
#: validation error rather than an unroutable number.
PhoneNumberProvider = Literal["twilio", "plivo", "exotel", "vobiz", "talko", "simulated"]

DEFAULT_PROVIDER: Final[PhoneNumberProvider] = "simulated"
DEFAULT_COUNTRY: Final[str] = "IN"
DEFAULT_STATUS: Final[str] = "active"

# --- Authorization scopes (unchanged from the legacy router, spec 0074) ----------------
READ_SCOPE: Final[str] = "platform:read"
WRITE_SCOPE: Final[str] = "platform:write"

# --- Talko inbound normalization --------------------------------------------------------
#: Country code India, the one the legacy normalizer special-cased.
INDIAN_COUNTRY_CODE: Final[str] = "91"
#: A full Indian DID: country code + 10 subscriber digits.
FULL_INDIAN_DIGITS_LENGTH: Final[int] = 12
#: A national number with a leading trunk zero (09179…).
TRUNK_PREFIXED_LENGTH: Final[int] = 11
#: A bare 10-digit subscriber number.
SUBSCRIBER_DIGITS_LENGTH: Final[int] = 10

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
#: Never the digits — a DID is PII (AGENTS.md §4); the id is enough to find the row.
LOG_CREATED: Final[str] = "Phone number created (id %s)"
LOG_ASSIGNED: Final[str] = "Phone number %s assigned to agent %s"
LOG_UNASSIGNED: Final[str] = "Phone number %s unassigned"
LOG_DELETED: Final[str] = "Phone number %s deleted"
LOG_RESOLVED: Final[str] = "Resolved phone number %s to agent %s"
LOG_RESOLVE_MISS: Final[str] = "Phone number resolve miss (id %s)"
