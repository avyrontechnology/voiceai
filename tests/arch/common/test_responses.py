"""Response envelopes and the exception handlers — including the 500 that must not leak.

The handler tests run through a locally built `FastAPI` app rather than the project factory, so
they depend on nothing outside `voiceai.common`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.responses import JSONResponse

from voiceai.common.datetime_utils import UTC
from voiceai.common.errors import AppError, ConflictError, NotFoundError
from voiceai.common.logger import LOGGER_NAME, set_request_id
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.common.responses import (
    ApiMeta,
    error_payload,
    error_response,
    paginated_response,
    public_validation_errors,
    register_exception_handlers,
    success_response,
)

#: A marker that looks like the kind of string a real exception message would carry.
PLANTED_SECRET = "hunter2-style-secret"
#: Text only a validator raises; a client must never see it (spec 0052).
VALIDATOR_TEXT = "is revoked"
#: The only keys a 422 per-field record may carry (spec 0052).
RECORD_KEYS = {"loc", "type"}
#: Long enough to satisfy `Credentials.password`, so only the token validator fails.
VALID_PASSWORD = "p" * 16
#: The child of the project logger the handlers write their one line per failure to.
HANDLER_LOGGER = f"{LOGGER_NAME}.common.responses"


class Widget(BaseModel):
    """A payload model, to prove models and datetimes survive encoding."""

    name: str
    created_at: datetime


class Credentials(BaseModel):
    """A body whose rejected values must never be echoed (a signup's password, a token)."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=16)
    token: str = ""

    @field_validator("token")
    @classmethod
    def reject_any_token(cls, value: str) -> str:
        """Fail with text that embeds the value, the way a careless validator would."""
        if value:
            raise ValueError(f"token {value} {VALIDATOR_TEXT}")
        return value


class Batch(BaseModel):
    """A nested body, to prove list indexes survive in `loc`."""

    items: list[Credentials]


class ListHandler(logging.Handler):
    """Collect records instead of writing them anywhere."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store the record."""
        self.records.append(record)

    def lines(self) -> list[str]:
        """The formatted message of every collected record, in order."""
        return [record.getMessage() for record in self.records]


def body_of(response: JSONResponse) -> dict[str, Any]:
    """Decode a `JSONResponse` body built outside an HTTP round trip."""
    return cast("dict[str, Any]", json.loads(response.body))


@pytest.fixture
def handler_log() -> Iterator[ListHandler]:
    """Collect what the exception handlers log.

    Attached directly to the handlers' logger rather than through `caplog`: the `otobaai`
    family does not propagate to the root once it is configured (see `test_logger.py`).
    """
    logger = logging.getLogger(HANDLER_LOGGER)
    handler = ListHandler()
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(level)


@pytest.fixture
def envelope_app() -> FastAPI:
    """A minimal app whose routes raise one of each failure the handlers must translate."""
    app = FastAPI()

    @app.get("/app-error")
    async def raise_app_error() -> None:
        raise NotFoundError("agent not found")

    @app.get("/internal-app-error")
    async def raise_internal_app_error() -> None:
        raise AppError(f"connection string {PLANTED_SECRET}")

    @app.get("/http-error")
    async def raise_http_error() -> None:
        raise HTTPException(status_code=401, detail="token expired", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/upstream-error")
    async def raise_upstream_error() -> None:
        raise HTTPException(status_code=502, detail=f"upstream said {PLANTED_SECRET}")

    @app.get("/unprocessable")
    async def raise_unprocessable() -> None:
        raise HTTPException(status_code=422, detail="definition rejected")

    @app.get("/only-get")
    async def only_get() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/validated")
    async def validated(count: int) -> dict[str, int]:
        return {"count": count}

    @app.post("/credentials")
    async def credentials(payload: Credentials) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/batch")
    async def batch(payload: Batch) -> dict[str, int]:
        return {"count": len(payload.items)}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"db password is {PLANTED_SECRET}")

    register_exception_handlers(app)
    return app


class TestSuccessResponse:
    """The success envelope every endpoint answers with."""

    def test_default_shape(self) -> None:
        response = success_response({"id": "1"})
        assert response.status_code == 200
        assert body_of(response) == {
            "ok": True,
            "data": {"id": "1"},
            "message": None,
            "meta": {"request_id": None, "pagination": None},
        }

    def test_message_and_status_are_honoured(self) -> None:
        response = success_response(None, message="created", status_code=201)
        assert response.status_code == 201
        assert body_of(response)["message"] == "created"

    def test_models_and_datetimes_are_encoded(self) -> None:
        widget = Widget(name="w", created_at=datetime(2026, 9, 16, 10, 30, tzinfo=UTC))
        assert body_of(success_response(widget))["data"] == {
            "name": "w",
            "created_at": "2026-09-16T10:30:00Z",
        }

    def test_request_id_is_filled_in_from_the_context(self) -> None:
        set_request_id("req-42")
        assert body_of(success_response())["meta"]["request_id"] == "req-42"

    def test_meta_accepts_a_mapping(self) -> None:
        set_request_id("req-42")
        meta = body_of(success_response(meta={"pagination": {"total": 3}}))["meta"]
        assert meta == {"request_id": "req-42", "pagination": {"total": 3}}

    def test_explicit_request_id_wins_over_the_context(self) -> None:
        set_request_id("req-42")
        response = success_response(meta=ApiMeta(request_id="explicit"))
        assert body_of(response)["meta"]["request_id"] == "explicit"

    def test_caller_meta_is_not_mutated(self) -> None:
        meta = ApiMeta()
        set_request_id("req-42")
        success_response(meta=meta)
        assert meta.request_id is None


class TestErrorEnvelope:
    """The error envelope, built directly from an `AppError`."""

    def test_error_payload_shape(self) -> None:
        error = ConflictError("already exists")
        assert error_payload(error) == {
            "ok": False,
            "detail": "already exists",
            "error": error.to_dict(),
        }

    def test_error_response_uses_the_error_status(self) -> None:
        response = error_response(ConflictError("already exists"))
        assert response.status_code == 409
        assert body_of(response)["error"]["code"] == "conflict"

    def test_internal_errors_stay_opaque(self) -> None:
        error = AppError(f"leaky {PLANTED_SECRET}")
        payload = error_payload(error)
        assert PLANTED_SECRET not in json.dumps(payload)
        assert payload["error"]["error_id"] == error.error_id


class TestPaginatedResponse:
    """List endpoints put their counters in `meta.pagination`."""

    def test_items_become_data_and_counters_become_meta(self) -> None:
        page = paginate([1, 2], total=5, params=PaginationParams(page=1, page_size=2))
        body = body_of(paginated_response(page, message="listed"))
        assert body["data"] == [1, 2]
        assert body["message"] == "listed"
        assert body["meta"]["pagination"] == {
            "page": 1,
            "page_size": 2,
            "total": 5,
            "pages": 3,
            "has_next": True,
        }

    def test_request_id_is_included(self) -> None:
        set_request_id("req-7")
        page: Page[Any] = paginate([], total=0, params=PaginationParams())
        assert body_of(paginated_response(page))["meta"]["request_id"] == "req-7"


class TestPublicValidationErrors:
    """Pydantic's records are reduced to an allow-list: where it failed and which rule (spec 0052)."""

    def test_a_full_record_keeps_only_loc_and_type(self) -> None:
        record = {
            "type": "string_too_short",
            "loc": ("body", "password"),
            "msg": "String should have at least 16 characters",
            "input": PLANTED_SECRET,
            "ctx": {"min_length": 16},
            "url": "https://errors.pydantic.dev/2.13/v/string_too_short",
        }
        assert public_validation_errors([record]) == [{"loc": ["body", "password"], "type": "string_too_short"}]

    def test_unknown_keys_stay_server_side(self) -> None:
        # An allow-list: a key a future pydantic/FastAPI adds is dropped without a code change.
        record = {"type": "missing", "loc": ("query", "page"), "future_key": PLANTED_SECRET}
        assert public_validation_errors([record]) == [{"loc": ["query", "page"], "type": "missing"}]

    def test_order_and_cardinality_are_preserved(self) -> None:
        records = [{"type": "missing", "loc": ("body", name)} for name in ("b", "a", "b")]
        assert [record["loc"][-1] for record in public_validation_errors(records)] == ["b", "a", "b"]

    def test_indexes_stay_integers_and_other_segments_become_text(self) -> None:
        record = {"type": "string_type", "loc": ("body", "items", 1, 2.5)}
        assert public_validation_errors([record]) == [{"loc": ["body", "items", 1, "2.5"], "type": "string_type"}]

    def test_missing_keys_default_instead_of_raising(self) -> None:
        assert public_validation_errors([{"msg": PLANTED_SECRET}]) == [{"loc": [], "type": ""}]

    def test_no_records_is_an_empty_list(self) -> None:
        assert public_validation_errors([]) == []

    def test_a_real_pydantic_failure_reduces_the_same_way(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Credentials.model_validate({"password": PLANTED_SECRET[:4], "token": PLANTED_SECRET})
        reduced = public_validation_errors(caught.value.errors())
        assert reduced == [
            {"loc": ["password"], "type": "string_too_short"},
            {"loc": ["token"], "type": "value_error"},
        ]
        assert PLANTED_SECRET[:4] not in json.dumps(reduced)


class TestExceptionHandlers:
    """Everything that escapes a route comes back as the same envelope."""

    async def test_app_errors_keep_their_status_and_message(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/app-error")
        assert response.status_code == 404
        body = response.json()
        assert body["ok"] is False
        assert body["detail"] == "agent not found"
        assert body["error"]["code"] == "not_found"
        assert body["error"]["retryable"] is False

    async def test_internal_app_errors_are_opaque(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/internal-app-error")
        assert response.status_code == 500
        assert PLANTED_SECRET not in response.text
        assert response.json()["error"]["error_id"] in response.json()["detail"]

    async def test_http_exceptions_keep_detail_and_headers(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/http-error")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["detail"] == "token expired"
        assert response.json()["error"]["code"] == "unauthorized"

    async def test_server_side_http_exceptions_keep_their_status_but_lose_their_detail(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/upstream-error")
        assert response.status_code == 502
        assert PLANTED_SECRET not in response.text
        assert response.json()["error"]["code"] == "internal_error"

    async def test_router_404_goes_through_the_envelope(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/no-such-route")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_router_405_keeps_its_allow_header(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.post("/only-get")
        assert response.status_code == 405
        assert "GET" in response.headers["allow"]
        assert response.json()["ok"] is False

    async def test_validation_errors_become_a_422_envelope(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/validated", params={"count": "not-a-number"})
        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"ok", "detail", "error"}
        assert body["ok"] is False
        assert body["detail"] == "Request validation failed"
        assert set(body["error"]) == {"code", "message", "error_id", "retryable", "details"}
        assert body["error"]["code"] == "invalid_request"
        assert body["error"]["details"] == {"errors": [{"loc": ["query", "count"], "type": "int_parsing"}]}
        assert "not-a-number" not in response.text

    async def test_a_rejected_body_value_is_never_echoed(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        # The reproduction (spec 0052): pydantic's record carries the submitted value under
        # `input`, so a too-short password used to come back in the 422 body.
        async with client_factory(envelope_app) as client:
            response = await client.post("/credentials", json={"password": PLANTED_SECRET[:8]})
        assert response.status_code == 422
        assert response.json()["error"]["details"]["errors"] == [
            {"loc": ["body", "password"], "type": "string_too_short"}
        ]
        assert PLANTED_SECRET[:8] not in response.text
        assert "pydantic" not in response.text

    async def test_validator_text_never_reaches_the_client(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        payload = {"password": VALID_PASSWORD, "token": PLANTED_SECRET}
        async with client_factory(envelope_app) as client:
            response = await client.post("/credentials", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["details"]["errors"] == [{"loc": ["body", "token"], "type": "value_error"}]
        assert PLANTED_SECRET not in response.text
        assert VALIDATOR_TEXT not in response.text

    async def test_unknown_keys_are_named_but_their_values_are_not_echoed(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        payload = {"password": VALID_PASSWORD, "legacy_field": PLANTED_SECRET}
        async with client_factory(envelope_app) as client:
            response = await client.post("/credentials", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["details"]["errors"] == [
            {"loc": ["body", "legacy_field"], "type": "extra_forbidden"}
        ]
        assert PLANTED_SECRET not in response.text

    async def test_nested_failures_keep_their_list_index(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        payload = {"items": [{"password": VALID_PASSWORD}, {"password": PLANTED_SECRET[:8]}, {}]}
        async with client_factory(envelope_app) as client:
            response = await client.post("/batch", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["details"]["errors"] == [
            {"loc": ["body", "items", 1, "password"], "type": "string_too_short"},
            {"loc": ["body", "items", 2, "password"], "type": "missing"},
        ]
        assert PLANTED_SECRET[:8] not in response.text

    async def test_malformed_json_names_the_offset_and_nothing_else(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        raw = f'{{"password": "{PLANTED_SECRET}'.encode()
        async with client_factory(envelope_app) as client:
            response = await client.post("/credentials", content=raw, headers={"content-type": "application/json"})
        assert response.status_code == 422
        records = response.json()["error"]["details"]["errors"]
        assert [set(record) for record in records] == [RECORD_KEYS]
        assert records[0]["type"] == "json_invalid"
        assert records[0]["loc"][0] == "body"
        assert PLANTED_SECRET not in response.text
        assert "Unterminated" not in response.text

    async def test_unhandled_exceptions_never_leak_their_message(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        # Starlette's error middleware re-raises after responding, so the transport must be
        # told not to surface that exception (spec 0001, test plan).
        async with client_factory(envelope_app, raise_app_exceptions=False) as client:
            response = await client.get("/boom")
        assert response.status_code == 500
        assert PLANTED_SECRET not in response.text
        assert "RuntimeError" not in response.text
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert body["error"]["error_id"] in body["detail"]
        assert len(body["error"]["error_id"]) == 12


class TestHandlerLogLine:
    """The log line names the status the client received, so an operator can search by it."""

    async def test_a_validation_failure_logs_the_422_it_answered(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient], handler_log: ListHandler
    ) -> None:
        # `InvalidRequestError` is a 400 by class, but the response is a 422 (spec 0052): the
        # line used to read `status=400`, so a search for the status the caller saw found nothing.
        async with client_factory(envelope_app) as client:
            response = await client.post("/credentials", json={"password": PLANTED_SECRET[:8]})
        assert response.status_code == 422
        error_id = response.json()["error"]["error_id"]
        assert handler_log.lines() == [
            f"app_error error_id={error_id} code=invalid_request status=422 path=/credentials"
        ]
        assert handler_log.records[0].levelno == logging.WARNING
        assert handler_log.records[0].exc_info is None

    async def test_the_rejected_value_is_not_logged(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient], handler_log: ListHandler
    ) -> None:
        async with client_factory(envelope_app) as client:
            await client.get("/validated", params={"count": PLANTED_SECRET})
        assert len(handler_log.records) == 1
        assert PLANTED_SECRET not in handler_log.lines()[0]
        assert "status=422 path=/validated" in handler_log.lines()[0]

    async def test_an_http_exception_logs_its_own_status(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient], handler_log: ListHandler
    ) -> None:
        # A 422 raised as an `HTTPException` maps onto `InvalidRequestError` (400) as well.
        async with client_factory(envelope_app) as client:
            response = await client.get("/unprocessable")
        assert response.status_code == 422
        assert response.json()["detail"] == "definition rejected"
        assert len(handler_log.records) == 1
        assert "code=invalid_request status=422 path=/unprocessable" in handler_log.lines()[0]
        assert handler_log.records[0].levelno == logging.WARNING

    async def test_a_server_side_http_exception_logs_its_status_as_an_error(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient], handler_log: ListHandler
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/upstream-error")
        assert response.status_code == 502
        assert len(handler_log.records) == 1
        assert "status=502 path=/upstream-error" in handler_log.lines()[0]
        assert handler_log.records[0].levelno == logging.ERROR

    async def test_an_app_error_logs_its_own_status(
        self, envelope_app: FastAPI, client_factory: Callable[..., AsyncClient], handler_log: ListHandler
    ) -> None:
        async with client_factory(envelope_app) as client:
            response = await client.get("/app-error")
        assert response.status_code == 404
        assert len(handler_log.records) == 1
        assert "code=not_found status=404 path=/app-error" in handler_log.lines()[0]
