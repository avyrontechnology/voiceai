"""The FastAPI application factory (AGENTS.md rule 4).

`create_app` is the single place the web boundary is assembled: correlation ids in, module
routers mounted, error envelopes out, container closed on shutdown. There is no module-level
`app` object — a factory keeps tests free to build an app per case with fakes injected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Lifespan

from voiceai.common.constants import (
    API_PREFIX,
    APP_NAME,
    APP_VERSION,
    CONTAINER_STATE_ATTR,
    PRINCIPAL_STATE_ATTR,
    REQUEST_ID_HEADER,
    SESSION_COOKIE,
)
from voiceai.common.errors import AppError, ConfigurationError
from voiceai.common.logger import configure_logging, get_logger, get_request_id, set_request_id
from voiceai.common.responses import register_exception_handlers, unexpected_error_response
from voiceai.common.security import REQUEST_ID_PATTERN
from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext, bind_tenant
from voiceai.core.container import VoiceAIContainer, aclose_container, build_container
from voiceai.core.environment import Environment, ensure_exact_origins, get_environment

if TYPE_CHECKING:  # import-direction rule: modules → core is the only static direction
    from voiceai.modules import ModuleDef

__all__ = ["RequestIdMiddleware", "TenantMiddleware", "create_app"]

#: Methods and headers the browser boundary accepts when CORS is enabled at all.
CORS_ALLOW_METHODS: Final[tuple[str, ...]] = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS: Final[tuple[str, ...]] = ("Authorization", "Content-Type", REQUEST_ID_HEADER)
#: Exposed so a browser client can read back the id it was assigned and quote it in a report.
CORS_EXPOSE_HEADERS: Final[tuple[str, ...]] = (REQUEST_ID_HEADER,)


def _resolve_request_id(inbound: str | None) -> str:
    """Decide the correlation id for a request.

    An inbound `X-Request-ID` is attacker-controlled and ends up in log files, so it is only
    honoured when it matches `REQUEST_ID_PATTERN`; anything else is silently replaced rather
    than rejected, because a malformed header must not fail an otherwise valid request.
    `fullmatch` rather than `match`: Python's `$` also matches before a trailing newline, and a
    newline inside a request id is log injection.

    Args:
        inbound: The raw header value, if the client sent one.

    Returns:
        The trusted id to log and echo.
    """
    if inbound and REQUEST_ID_PATTERN.fullmatch(inbound):
        return inbound
    return uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a validated correlation id to the request context and echo it back.

    This middleware also owns the unexpected-exception (500) envelope: producing it *here*,
    inside the middleware stack, means the response still travels out through `CORSMiddleware`
    and still gets the `X-Request-ID` header below — the handler on Starlette's outermost
    `ServerErrorMiddleware` runs outside both and remains only a backstop.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Set the request id for the duration of the request and add it to the response.

        `AppError` and `HTTPException` are deliberately not caught: the inner
        `ExceptionMiddleware` already converts those into envelopes before they can reach this
        middleware, so anything arriving here is either an unexpected exception (turned into
        the opaque 500 envelope) or a rare escapee re-raised to the server-error backstop.

        Args:
            request: The incoming request.
            call_next: The rest of the application stack.

        Returns:
            The downstream response, carrying the `X-Request-ID` header.
        """
        request_id = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        set_request_id(request_id)
        try:
            response = await call_next(request)
        except (AppError, StarletteHTTPException):
            raise
        except Exception as exc:  # the catch-all envelope must carry CORS + correlation headers
            response = unexpected_error_response(exc, path=request.url.path)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class TenantMiddleware(BaseHTTPMiddleware):
    """Bind the ambient tenant context for the duration of the request (spec 0020, M1b).

    Credentials resolve through the container's ``tenant_resolver`` (the auth
    service's non-raising ``resolve_request_identity``) — this middleware never
    imports a module, keeping the core → modules direction one-way. The resolved
    principal is stashed on ``request.state`` so controllers resolve each
    request's credentials once, not twice. Anonymous or invalid credentials bind
    the system tenant with empty scopes, so ``current_tenant()`` stays total
    behind the middleware while every auth gate downstream still rejects
    scope-less callers. Backend failures propagate to the 500 envelope instead
    of demoting traffic to anonymous (fail-closed).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Resolve the caller and bind its tenant around the rest of the stack.

        Args:
            request: The incoming request.
            call_next: The rest of the application stack.

        Returns:
            The downstream response.

        Raises:
            ConfigurationError: When the app was built without a container or the
                container exposes no tenant resolver — a wiring bug, opaque 500.
        """
        container = getattr(request.app.state, CONTAINER_STATE_ATTR, None)
        provider = getattr(container, "tenant_resolver", None) if container is not None else None
        if provider is None:
            raise ConfigurationError("tenant resolution is not wired")
        request_id = get_request_id() or ""
        resolve = provider()
        context, principal = await resolve(
            request.cookies.get(SESSION_COOKIE),
            request.headers.get("authorization", ""),
            request_id,
        )
        setattr(request.state, PRINCIPAL_STATE_ATTR, principal)
        if context is None:
            context = TenantContext(tenant_id=SYSTEM_TENANT_ID, request_id=request_id)
        with bind_tenant(context):
            return await call_next(request)


def _build_lifespan(container: VoiceAIContainer) -> Lifespan[FastAPI]:
    """Build the lifespan handler: seed globals on startup, release on shutdown.

    Startup syncs the provider catalog (spec 0022): the seed is version-aware
    and idempotent, so steady-state boots cost one bounded read. A seed failure
    logs loudly but never blocks boot — the catalog is non-critical (reads
    serve empty, validation skips when empty) and converges on the next boot.

    Args:
        container: The container owning the process's infrastructure clients.

    Returns:
        A lifespan context manager for `FastAPI(lifespan=...)`.
    """
    boot_logger = get_logger("core.app_factory")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await container.catalog_service().ensure_seeded()
        except Exception as exc:  # noqa: BLE001 - catalog must never block boot
            boot_logger.warning("catalog seeding skipped: %s", type(exc).__name__)
        try:
            # Tools seed under a system binding (spec 0029): the service
            # constructor reads the ambient tenant, and seeding writes system
            # rows through the system view only.
            with bind_tenant(TenantContext(tenant_id=SYSTEM_TENANT_ID, request_id="boot")):
                await container.tools_service().ensure_seeded()
        except Exception as exc:  # noqa: BLE001 - tools must never block boot
            boot_logger.warning("tools seeding skipped: %s", type(exc).__name__)
        try:
            yield
        finally:
            # try/finally: the clients are released even when the lifespan scope is cancelled
            # or an exception is thrown into it, not only on a clean shutdown.
            await aclose_container(container)

    return lifespan


def _add_cors(app: FastAPI, env: Environment) -> None:
    """Install CORS, but only when an explicit origin allowlist exists.

    On starlette 0.35.1, `allow_origins=["*"]` with `allow_credentials=True` is **not**
    neutralised by browsers: the middleware reflects the concrete requesting origin, so a
    wildcard would grant every site on the web credentialed access. That is why the
    `Environment` model rejects wildcards and non-origin entries on every construction path,
    why this function re-checks the list at the point it takes effect (covering
    `model_copy(update=...)`, which skips pydantic validators), and why the middleware is
    simply absent when the list is empty (AGENTS.md §4).

    Args:
        app: The application being built.
        env: The configuration carrying `allowed_origins`.
    """
    if not env.allowed_origins:
        return
    ensure_exact_origins(env.allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(env.allowed_origins),
        allow_credentials=True,
        allow_methods=list(CORS_ALLOW_METHODS),
        allow_headers=list(CORS_ALLOW_HEADERS),
        expose_headers=list(CORS_EXPOSE_HEADERS),
    )


def create_app(
    env: Environment | None = None,
    *,
    container: VoiceAIContainer | None = None,
    modules: Sequence[ModuleDef] | None = None,
) -> FastAPI:
    """Build the API application.

    Args:
        env: Explicit configuration; `None` uses the cached process environment.
        container: A pre-built container (tests inject fakes this way); `None` builds one.
        modules: Explicit module definitions; `None` uses the project registry.

    Returns:
        A configured `FastAPI` app whose `state.container` is the composition root.
    """
    environment = env if env is not None else get_environment()
    configure_logging(environment.log_level)
    resolved = container if container is not None else build_container(environment)
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=_build_lifespan(resolved))
    setattr(app.state, CONTAINER_STATE_ATTR, resolved)
    app.add_middleware(TenantMiddleware)
    # TenantMiddleware is added before RequestIdMiddleware on purpose: the last
    # add_middleware is the outermost, so RequestId runs first and the tenant
    # context inherits the validated correlation id.
    app.add_middleware(RequestIdMiddleware)
    # Added after RequestIdMiddleware on purpose: the last add_middleware is the outermost, so
    # CORS wraps RequestIdMiddleware and even the 500 envelopes built inside it carry the CORS
    # headers a browser needs to read them.
    _add_cors(app, environment)
    register_exception_handlers(app)

    modules_to_load: Sequence[ModuleDef]
    if modules is None:
        from voiceai.modules import ALL_MODULES

        modules_to_load = ALL_MODULES
    else:
        modules_to_load = modules

    for module in modules_to_load:
        app.include_router(module.router, prefix=API_PREFIX)

    return app
