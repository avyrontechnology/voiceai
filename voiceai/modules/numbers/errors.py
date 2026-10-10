"""Phone number error classes (spec 0074)."""

from voiceai.common.errors import AppError, NotFoundError


class NumbersError(AppError):
    """Base error for the numbers module (raised across layer boundaries)."""


class PhoneNumberNotFoundError(NotFoundError):
    """Raised when a requested phone number does not exist in the tenant."""

    def __init__(self, number_id: str) -> None:
        super().__init__(f"Phone number not found: {number_id}", details={"number_id": number_id})


class NumberUnassignedError(NotFoundError):
    """Raised when a DID exists but has no agent, so inbound cannot be routed.

    Extends `NotFoundError` rather than the module base so it serialises as a
    404 — the same status as a missing DID. The Talko resolve contract is
    fail-closed and must not reveal that an unassigned DID is in the system
    (spec 0074, security notes); a 500 here would both leak that and make Talko
    treat a routable-but-unassigned number as an outage.
    """

    def __init__(self, number_id: str) -> None:
        super().__init__(f"Phone number not found: {number_id}", details={"number_id": number_id})
