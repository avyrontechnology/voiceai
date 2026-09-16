"""The single `otobaai` logger: idempotent configuration and request-id enrichment.

Handlers are attached **directly to the `otobaai` logger** rather than using `caplog`: importing
anything under `voiceai` runs the legacy package `__init__` (a `basicConfig` plus a LogRecord
factory swap), so the project logger deliberately does not propagate to the root and root-level
capture would see nothing (spec 0001, test plan).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from voiceai.common.constants import LOG_HANDLER_NAME, NO_REQUEST_ID
from voiceai.common.logger import (
    LOGGER_NAME,
    RequestIdFilter,
    configure_logging,
    get_logger,
    get_request_id,
    set_request_id,
)


class ListHandler(logging.Handler):
    """Collect records instead of writing them anywhere."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store the record."""
        self.records.append(record)


@pytest.fixture(autouse=True)
def restore_logger_state() -> Iterator[None]:
    """Snapshot and restore the `otobaai` logger: its configuration is process-global."""
    logger = logging.getLogger(LOGGER_NAME)
    handlers = list(logger.handlers)
    filters = list(logger.filters)
    level, propagate = logger.level, logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.filters[:] = filters
    logger.setLevel(level)
    logger.propagate = propagate
    set_request_id(None)


@pytest.fixture
def captured() -> Iterator[ListHandler]:
    """Attach a collecting handler to the project logger for the duration of a test."""
    logger = logging.getLogger(LOGGER_NAME)
    handler = ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)


class TestGetLogger:
    """Naming rules — rule 3 allows exactly one family of logger names."""

    def test_root_logger_is_the_project_name(self) -> None:
        assert get_logger().name == "otobaai"

    def test_module_loggers_are_children(self) -> None:
        assert get_logger("core.container").name == "otobaai.core.container"

    def test_repeated_calls_return_the_same_logger(self) -> None:
        assert get_logger("core.db") is get_logger("core.db")

    def test_every_returned_logger_carries_the_request_id_filter(self) -> None:
        logger = get_logger("common.security")
        assert sum(isinstance(item, RequestIdFilter) for item in logger.filters) == 1

    def test_the_filter_is_not_added_twice(self) -> None:
        get_logger("common.security")
        logger = get_logger("common.security")
        assert sum(isinstance(item, RequestIdFilter) for item in logger.filters) == 1


class TestConfigureLogging:
    """Configuration must survive being called by every app and container build."""

    def test_installs_exactly_one_named_handler(self) -> None:
        configure_logging()
        configure_logging()
        configure_logging("DEBUG")
        logger = logging.getLogger(LOGGER_NAME)
        assert sum(handler.name == LOG_HANDLER_NAME for handler in logger.handlers) == 1

    def test_does_not_propagate_into_the_legacy_root_configuration(self) -> None:
        configure_logging()
        assert logging.getLogger(LOGGER_NAME).propagate is False

    def test_level_is_applied(self) -> None:
        configure_logging("WARNING")
        assert logging.getLogger(LOGGER_NAME).level == logging.WARNING

    def test_unknown_level_falls_back_to_info(self) -> None:
        configure_logging("NOT-A-LEVEL")
        assert logging.getLogger(LOGGER_NAME).level == logging.INFO

    def test_level_names_are_case_insensitive(self) -> None:
        configure_logging("debug")
        assert logging.getLogger(LOGGER_NAME).level == logging.DEBUG


class TestRequestId:
    """The contextvar that puts a correlation id on every line."""

    def test_round_trips_through_the_contextvar(self) -> None:
        set_request_id("req-42")
        assert get_request_id() == "req-42"

    def test_can_be_cleared(self) -> None:
        set_request_id("req-42")
        set_request_id(None)
        assert get_request_id() is None

    def test_records_from_the_root_logger_carry_it(self, captured: ListHandler) -> None:
        set_request_id("req-42")
        get_logger().info("hello")
        # `request_id` is stamped on dynamically by `RequestIdFilter`, hence getattr.
        assert [getattr(record, "request_id") for record in captured.records] == ["req-42"]  # noqa: B009

    def test_records_from_child_loggers_carry_it(self, captured: ListHandler) -> None:
        set_request_id("req-7")
        get_logger("core.container").warning("hello")
        assert getattr(captured.records[0], "request_id") == "req-7"  # noqa: B009

    def test_records_without_a_request_id_use_the_placeholder(self, captured: ListHandler) -> None:
        set_request_id(None)
        get_logger().info("hello")
        assert getattr(captured.records[0], "request_id") == NO_REQUEST_ID  # noqa: B009

    def test_the_configured_formatter_renders_the_id(self, captured: ListHandler) -> None:
        configure_logging("DEBUG")
        set_request_id("req-99")
        get_logger("common.responses").info("payload sent")
        stream_handler = next(
            handler for handler in logging.getLogger(LOGGER_NAME).handlers if handler.name == LOG_HANDLER_NAME
        )
        formatted = stream_handler.format(captured.records[0])
        assert "[req-99]" in formatted
        assert "otobaai.common.responses: payload sent" in formatted
