"""Response envelopes and the exception handlers — including the 500 that must not leak.

The handler tests run through a locally built `FastAPI` app rather than the project factory, so
they depend on nothing outside `voiceai.common`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from pydantic import BaseModel
from starlette.responses import JSONResponse

from voiceai.common.datetime_utils import UTC
from voiceai.common.errors import AppError, ConflictError, NotFoundError
from voiceai.common.logger import set_request_id
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.common.responses import (
    ApiMeta,
    error_payload,
    error_response,
    paginated_response,
    register_exception_handlers,
    success_response,
)

#: A marker that looks like the kind of string a real exception message would carry.
PLANTED_SECRET = "hunter2-style-secret"


class Widget(BaseModel):
    """A payload model, to prove models and datetimes survive encoding."""

    name: str
    created_at: datetime


def body_of(response: JSONResponse) -> dict[str, Any]:
    """Decode a `JSONResponse` body built outside an HTTP round trip."""
    return cast("dict[str, Any]", json.loads(response.body))


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

    @app.get("/only-get")
    async def only_get() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/validated")
    async def validated(count: int) -> dict[str, int]:
        return {"count": count}

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
        assert body["error"]["code"] == "invalid_request"
        assert body["error"]["details"]["errors"][0]["loc"] == ["query", "count"]

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
