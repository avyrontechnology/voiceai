"""The `AppError` taxonomy: codes, statuses, opacity of internal errors, and serialisation."""

from __future__ import annotations

import re

import pytest

from voiceai.common.constants import ERROR_ID_LENGTH
from voiceai.common.errors import (
    AppError,
    ConfigurationError,
    ConflictError,
    DatabaseError,
    DependencyUnavailableError,
    ErrorCode,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)

LOWER_SNAKE = re.compile(r"^[a-z]+(_[a-z]+)*$")


class TestErrorCode:
    """The wire codes clients branch on."""

    def test_every_value_is_lower_snake(self) -> None:
        for code in ErrorCode:
            assert LOWER_SNAKE.match(code.value), code

    def test_codes_compare_as_plain_strings(self) -> None:
        # Widened to `str` on purpose: the point is that a str-enum member equals its value.
        code: str = ErrorCode.NOT_FOUND
        assert code == "not_found"


class TestAppError:
    """Construction, opacity and serialisation of the base error."""

    def test_error_id_is_a_short_hex_slice(self) -> None:
        error = AppError("boom")
        assert len(error.error_id) == ERROR_ID_LENGTH
        assert re.fullmatch(r"[0-9a-f]+", error.error_id)

    def test_error_ids_are_unique_per_instance(self) -> None:
        assert AppError("boom").error_id != AppError("boom").error_id

    def test_internal_errors_never_expose_their_message(self) -> None:
        error = AppError("connection string postgres://user:pw@host failed")
        assert "postgres" not in error.public_message
        assert error.error_id in error.public_message

    def test_expected_errors_expose_their_message(self) -> None:
        assert NotFoundError("agent not found").public_message == "agent not found"

    def test_message_is_kept_for_logs_even_when_hidden(self) -> None:
        error = AppError("internal detail")
        assert error.message == "internal detail"
        assert str(error) == "internal detail"

    def test_to_dict_carries_the_public_contract(self) -> None:
        error = NotFoundError("agent not found")
        assert error.to_dict() == {
            "code": "not_found",
            "message": "agent not found",
            "error_id": error.error_id,
            "retryable": False,
        }

    def test_to_dict_omits_empty_details(self) -> None:
        assert "details" not in AppError("boom").to_dict()

    def test_to_dict_includes_details_when_present(self) -> None:
        error = InvalidRequestError("bad field", details={"field": "name"})
        assert error.to_dict()["details"] == {"field": "name"}

    def test_details_are_copied_not_aliased(self) -> None:
        details = {"field": "name"}
        error = InvalidRequestError("bad field", details=details)
        details["field"] = "mutated"
        assert error.details == {"field": "name"}

    def test_cause_is_chained_for_the_log_stack(self) -> None:
        cause = ValueError("root cause")
        error = AppError("wrapped", cause=cause)
        assert error.__cause__ is cause

    def test_subclasses_are_catchable_as_app_error(self) -> None:
        with pytest.raises(AppError):
            raise ConflictError("duplicate")


@pytest.mark.parametrize(
    ("error_type", "status", "code", "retryable"),
    [
        (InvalidRequestError, 400, ErrorCode.INVALID_REQUEST, False),
        (UnauthorizedError, 401, ErrorCode.UNAUTHORIZED, False),
        (ForbiddenError, 403, ErrorCode.FORBIDDEN, False),
        (NotFoundError, 404, ErrorCode.NOT_FOUND, False),
        (ConflictError, 409, ErrorCode.CONFLICT, False),
        (RateLimitedError, 429, ErrorCode.RATE_LIMITED, True),
        (DependencyUnavailableError, 503, ErrorCode.DEPENDENCY_UNAVAILABLE, True),
        (DatabaseError, 500, ErrorCode.DATABASE_ERROR, False),
        (ConfigurationError, 500, ErrorCode.CONFIGURATION_ERROR, False),
        (AppError, 500, ErrorCode.INTERNAL_ERROR, False),
    ],
)
def test_subclass_maps_to_its_status_and_code(
    error_type: type[AppError],
    status: int,
    code: ErrorCode,
    retryable: bool,
) -> None:
    error = error_type("boom")
    assert (error.http_status, error.code, error.retryable) == (status, code, retryable)


class TestConfigurationError:
    """The extra `path` argument operators use to find the broken knob."""

    def test_path_lands_in_details(self) -> None:
        error = ConfigurationError("bad value", path="COOKIE_SECURE")
        assert error.details["path"] == "COOKIE_SECURE"
        assert error.to_dict()["details"] == {"path": "COOKIE_SECURE"}

    def test_details_stay_empty_without_a_path(self) -> None:
        assert ConfigurationError("bad value").details == {}

    def test_path_merges_with_other_details(self) -> None:
        error = ConfigurationError("bad value", path="DB_BACKEND", details={"backend": "mongo"})
        assert error.details == {"backend": "mongo", "path": "DB_BACKEND"}

    def test_message_is_public_because_it_describes_our_own_config(self) -> None:
        assert ConfigurationError("DB_BACKEND must be memory").public_message == "DB_BACKEND must be memory"
