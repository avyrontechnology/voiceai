"""Auth error hierarchy: each class carries its legacy HTTP status (C0).

The C6 controller map test will enumerate this same table from the mapper side;
this file pins the error side so status drift fails here first.
"""

import pytest

from voiceai.modules.auth.errors import (
    AuthError,
    AuthNotFoundError,
    ForbiddenError,
    InviteInvalidError,
    InvalidCredentialsError,
    TooManyAttemptsError,
)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InvalidCredentialsError("nope"), 401),
        (ForbiddenError("nope"), 403),
        (TooManyAttemptsError("slow down"), 429),
        (InviteInvalidError("bad invite"), 400),
        (AuthNotFoundError("missing"), 404),
    ],
)
def test_each_error_carries_its_legacy_status(error: AuthError, status: int) -> None:
    """Status codes are the contract status-asserting suites pin."""
    assert error.http_status == status
    assert isinstance(error, AuthError)
