"""C6: the controller's error→status map covers every service error (spec 0005, R2).

Two halves: the map renders each error at its legacy status with the detail string
preserved in the envelope, and the mapped set is COMPLETE — every concrete error
the auth code can raise (module hierarchy plus the shared errors the service and
controller import) appears exactly once, so a new error fails here until it is
mapped and pinned.
"""

from __future__ import annotations

import inspect
import json

import pytest

import voiceai.modules.auth.errors as auth_errors
from voiceai.common.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
)
from voiceai.modules.auth import controller
from voiceai.modules.auth.errors import (
    AuthError,
    AuthNotFoundError,
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
    TooManyAttemptsError,
)

#: Every raisable error → its legacy HTTP status (the C6 map, enumerated).
ERROR_MAP: tuple[tuple[type[AppError], int], ...] = (
    (InvalidCredentialsError, 401),
    (AuthError, 401),
    (ForbiddenError, 403),
    (InviteInvalidError, 400),
    (InvalidRequestError, 400),
    (AuthNotFoundError, 404),
    (ConflictError, 409),
    (TooManyAttemptsError, 429),
    (DependencyUnavailableError, 503),
)


def _concrete_errors() -> set[type[AppError]]:
    """Every concrete error class the auth code can raise."""
    module_errors = {
        member
        for _, member in inspect.getmembers(auth_errors, inspect.isclass)
        if issubclass(member, AppError) and member.__module__.startswith("voiceai.modules.auth")
    }
    shared = {ConflictError, InvalidRequestError, DependencyUnavailableError}
    return module_errors | shared


def test_map_covers_every_raisable_error_exactly_once() -> None:
    """Completeness: mapped set == raisable set, no gaps, no extras, no dupes."""
    mapped = [error for error, _ in ERROR_MAP]

    assert len(mapped) == len(set(mapped))  # no duplicate rows
    assert set(mapped) == _concrete_errors()


@pytest.mark.parametrize(("error_cls", "status"), ERROR_MAP)
def test_to_http_preserves_status_and_detail(error_cls: type[AppError], status: int) -> None:
    """The mapper renders the legacy status with the message as envelope detail."""
    err = error_cls("probe detail")

    response = controller._to_http(err)
    body = json.loads(response.body)

    assert response.status_code == status == err.http_status
    assert body["ok"] is False
    assert body["detail"] == "probe detail"
