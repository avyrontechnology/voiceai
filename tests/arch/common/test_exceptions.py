"""The guard helpers: they must raise `AppError`s, never builtins."""

from __future__ import annotations

import pytest

from voiceai.common.errors import AppError, ConflictError, InvalidRequestError, NotFoundError
from voiceai.common.exceptions import ensure, ensure_found, ensure_valid


class TestEnsure:
    """`ensure` — the generic guard."""

    def test_passes_silently_when_the_condition_holds(self) -> None:
        ensure(True, "never raised")  # must simply not raise

    def test_raises_invalid_request_by_default(self) -> None:
        with pytest.raises(InvalidRequestError) as raised:
            ensure(False, "name is required")
        assert raised.value.message == "name is required"

    def test_raises_the_requested_error_type(self) -> None:
        with pytest.raises(ConflictError):
            ensure(False, "already exists", error=ConflictError)

    def test_raised_error_is_an_app_error(self) -> None:
        with pytest.raises(AppError):
            ensure(False, "boom", error=NotFoundError)


class TestEnsureFound:
    """`ensure_found` — the lookup guard that also narrows the type."""

    def test_returns_the_value_untouched(self) -> None:
        value = {"id": "1"}
        assert ensure_found(value, "missing") is value

    def test_raises_not_found_for_none(self) -> None:
        with pytest.raises(NotFoundError) as raised:
            ensure_found(None, "agent not found")
        assert raised.value.public_message == "agent not found"

    def test_falsy_but_present_values_survive(self) -> None:
        assert ensure_found(0, "missing") == 0
        assert ensure_found("", "missing") == ""


class TestEnsureValid:
    """`ensure_valid` — the validation guard."""

    def test_passes_when_valid(self) -> None:
        ensure_valid(True, "never raised")  # must simply not raise

    def test_raises_invalid_request(self) -> None:
        with pytest.raises(InvalidRequestError) as raised:
            ensure_valid(False, "page must be positive")
        assert raised.value.http_status == 400
