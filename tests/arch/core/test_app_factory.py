"""The application factory: wiring, correlation ids, CORS, envelopes and shutdown.

Every app here is built with an explicit module list (a dummy one or none), so these tests are
independent of whatever real modules exist in the registry.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient

from voiceai.common.errors import ConfigurationError
from voiceai.common.logger import LOGGER_NAME
from voiceai.core.app_factory import create_app
from voiceai.core.container import Container, build_container
from voiceai.core.environment import Environment

if TYPE_CHECKING:  # annotation only: collecting this file must never import voiceai.modules
    from voiceai.modules import ModuleDef

ORIGIN = "https://app.test"
HEX_32 = re.compile(r"^[0-9a-f]{32}$")
#: A marker that looks like the internals a real crash message would leak.
PLANTED_SECRET = "hunter2-style-secret"
BOOM_ROUTE = "/api/v1/boom"


class RecordingHandler(logging.Handler):
    """Collects records emitted by the `otobaai` logger (it does not propagate to root)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store the record for the test to inspect."""
        self.records.append(record)


@pytest.fixture
def otobaai_records() -> Iterator[list[logging.LogRecord]]:
    """Attach a recording handler directly to the `otobaai` logger for one test."""
    handler = RecordingHandler()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


@pytest.fixture
def boom_module() -> ModuleDef:
    """A module whose only route raises an unexpected exception carrying a marker."""
    router = APIRouter(prefix="/boom")

    @router.get("")
    async def boom() -> None:
        raise RuntimeError(f"db password is {PLANTED_SECRET}")

    return cast("ModuleDef", SimpleNamespace(name="boom", router=router, register=lambda _container: None))


@pytest.fixture
def app(arch_environment: Environment, dummy_module: ModuleDef) -> FastAPI:
    """The project app carrying one dummy module."""
    return create_app(env=arch_environment, modules=[dummy_module])


class TestComposition:
    """What `create_app` wires together."""

    def test_the_container_is_published_on_app_state(self, app: FastAPI) -> None:
        assert isinstance(app.state.container, Container)

    def test_a_supplied_container_is_reused(self, arch_environment: Environment, dummy_module: ModuleDef) -> None:
        container = build_container(arch_environment, modules=[dummy_module])
        built = create_app(env=arch_environment, container=container, modules=[dummy_module])
        assert built.state.container is container

    def test_the_app_is_named_after_the_project(self, app: FastAPI) -> None:
        assert app.title == "otoba-ai"

    async def test_module_routers_are_mounted_under_the_api_prefix(
        self, app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/dummy")
        assert response.status_code == 200
        assert response.json() == {"dummy": True}

    async def test_unknown_routes_answer_with_the_error_envelope(
        self, app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestRequestId:
    """`X-Request-ID` — echoed when trustworthy, regenerated when not."""

    async def test_a_valid_inbound_id_is_echoed(self, app: FastAPI, client_factory: Callable[..., AsyncClient]) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/dummy", headers={"X-Request-ID": "req-42"})
        assert response.headers["x-request-id"] == "req-42"

    async def test_a_missing_id_is_generated(self, app: FastAPI, client_factory: Callable[..., AsyncClient]) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/dummy")
        assert HEX_32.match(response.headers["x-request-id"])

    @pytest.mark.parametrize("hostile", ["bad id", "a" * 65, "drop;table", "req\nid", ""])
    async def test_a_hostile_id_is_replaced_not_echoed(
        self, app: FastAPI, client_factory: Callable[..., AsyncClient], hostile: str
    ) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/dummy", headers={"X-Request-ID": hostile})
        echoed = response.headers["x-request-id"]
        assert echoed != hostile
        assert HEX_32.match(echoed)

    async def test_error_responses_are_correlated_too(
        self, app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/missing", headers={"X-Request-ID": "req-99"})
        assert response.headers["x-request-id"] == "req-99"


class TestCors:
    """CORS is installed only behind an explicit allowlist."""

    async def test_absent_when_no_origins_are_configured(
        self, app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(app) as client:
            response = await client.get("/api/v1/dummy", headers={"Origin": ORIGIN})
        assert "access-control-allow-origin" not in response.headers

    async def test_allowed_origin_is_reflected_with_credentials(
        self,
        arch_environment: Environment,
        dummy_module: ModuleDef,
        client_factory: Callable[..., AsyncClient],
    ) -> None:
        env = arch_environment.model_copy(update={"allowed_origins": [ORIGIN]})
        cors_app = create_app(env=env, modules=[dummy_module])
        async with client_factory(cors_app) as client:
            response = await client.get("/api/v1/dummy", headers={"Origin": ORIGIN})
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    async def test_an_unlisted_origin_is_not_reflected(
        self,
        arch_environment: Environment,
        dummy_module: ModuleDef,
        client_factory: Callable[..., AsyncClient],
    ) -> None:
        env = arch_environment.model_copy(update={"allowed_origins": [ORIGIN]})
        cors_app = create_app(env=env, modules=[dummy_module])
        async with client_factory(cors_app) as client:
            response = await client.get("/api/v1/dummy", headers={"Origin": "https://evil.test"})
        assert "access-control-allow-origin" not in response.headers

    async def test_preflight_advertises_the_request_id_header(
        self,
        arch_environment: Environment,
        dummy_module: ModuleDef,
        client_factory: Callable[..., AsyncClient],
    ) -> None:
        env = arch_environment.model_copy(update={"allowed_origins": [ORIGIN]})
        cors_app = create_app(env=env, modules=[dummy_module])
        async with client_factory(cors_app) as client:
            response = await client.options(
                "/api/v1/dummy",
                headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "X-Request-ID",
                },
            )
        assert response.status_code == 200
        assert "x-request-id" in response.headers["access-control-allow-headers"].lower()


class TestUnexpectedErrors:
    """The 500 envelope is produced inside the middleware stack, not outside it."""

    @pytest.fixture
    def cors_boom_app(self, arch_environment: Environment, boom_module: ModuleDef) -> FastAPI:
        """An app with a crashing route and an explicit CORS allowlist."""
        env = arch_environment.model_copy(update={"allowed_origins": [ORIGIN]})
        return create_app(env=env, modules=[boom_module])

    async def test_a_500_carries_the_request_id_and_cors_headers(
        self, cors_boom_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(cors_boom_app, raise_app_exceptions=False) as client:
            response = await client.get(BOOM_ROUTE, headers={"Origin": ORIGIN, "X-Request-ID": "req-500"})
        assert response.status_code == 500
        assert response.headers["x-request-id"] == "req-500"
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    async def test_the_500_body_stays_opaque(
        self, cors_boom_app: FastAPI, client_factory: Callable[..., AsyncClient]
    ) -> None:
        async with client_factory(cors_boom_app, raise_app_exceptions=False) as client:
            response = await client.get(BOOM_ROUTE, headers={"Origin": ORIGIN})
        assert PLANTED_SECRET not in response.text
        assert "RuntimeError" not in response.text
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert body["error"]["error_id"] in body["detail"]

    async def test_the_500_is_logged_at_error_with_the_stack_and_the_error_id(
        self,
        cors_boom_app: FastAPI,
        client_factory: Callable[..., AsyncClient],
        otobaai_records: list[logging.LogRecord],
    ) -> None:
        async with client_factory(cors_boom_app, raise_app_exceptions=False) as client:
            response = await client.get(BOOM_ROUTE)
        error_id = response.json()["error"]["error_id"]
        matching = [record for record in otobaai_records if record.levelno == logging.ERROR]
        assert len(matching) == 1  # the middleware logs it; the backstop must not log it again
        record = matching[0]
        assert error_id in record.getMessage()
        assert record.exc_info is not None
        assert record.exc_info[0] is RuntimeError


class TestLifespan:
    """Shutdown releases the container's clients."""

    async def test_shutdown_closes_the_redis_client(
        self,
        arch_environment: Environment,
        dummy_module: ModuleDef,
        fake_redis: object,
        container_override: Callable[..., None],
    ) -> None:
        built = create_app(env=arch_environment, modules=[dummy_module])
        container_override(built, "redis", fake_redis)
        built.state.container.resolve("redis")  # the client only exists once something asked for it
        async with built.router.lifespan_context(built):
            pass
        assert getattr(fake_redis, "closed") is True

    async def test_a_thrown_into_lifespan_still_closes_the_container(
        self,
        arch_environment: Environment,
        dummy_module: ModuleDef,
        fake_redis: object,
        container_override: Callable[..., None],
    ) -> None:
        built = create_app(env=arch_environment, modules=[dummy_module])
        container_override(built, "redis", fake_redis)
        built.state.container.resolve("redis")
        with pytest.raises(RuntimeError, match="startup interrupted"):
            async with built.router.lifespan_context(built):
                raise RuntimeError("startup interrupted")
        assert getattr(fake_redis, "closed") is True


class TestCorsDefenseInDepth:
    """`_add_cors` re-validates the allowlist, closing pydantic's `model_copy` validation gap."""

    def test_model_copy_injected_wildcard_is_rejected_at_create_app(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"allowed_origins": ("*",)})
        with pytest.raises(ConfigurationError):
            create_app(env, modules=[])
