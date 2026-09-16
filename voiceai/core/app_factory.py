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
    REQUEST_ID_HEADER,
)
from voiceai.common.errors import AppError
from voiceai.common.logger import configure_logging, set_request_id
from voiceai.common.responses import register_exception_handlers, unexpected_error_response
from voiceai.common.security import REQUEST_ID_PATTERN
from voiceai.core.container import Container, build_container, resolve_modules
from voiceai.core.environment import Environment, ensure_exact_origins, get_environment

if TYPE_CHECKING:  # import-direction rule: modules → core is the only static direction
    from voiceai.modules import ModuleDef

__all__ = ["RequestIdMiddleware", "create_app"]

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


def _build_lifespan(container: Container) -> Lifespan[FastAPI]:
    """Build the lifespan handler that releases the container's clients on shutdown.

    Args:
        container: The container owning the process's infrastructure clients.

    Returns:
        A lifespan context manager for `FastAPI(lifespan=...)`.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # try/finally: the clients are released even when the lifespan scope is cancelled
            # or an exception is thrown into it, not only on a clean shutdown.
            await container.aclose()

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
    container: Container | None = None,
    modules: Sequence[ModuleDef] | None = None,
) -> FastAPI:
    """Build the API application.

    Args:
        env: Explicit configuration; `None` uses the cached process environment.
        container: A pre-built container (tests inject fakes this way); `None` builds one.
        modules: Explicit module definitions; `None` uses the project registry, imported
            lazily inside the call so `core` never depends on `modules` at import time.

    Returns:
        A configured `FastAPI` app whose `state.container` is the composition root.
    """
    environment = env if env is not None else get_environment()
    configure_logging(environment.log_level)
    resolved = container if container is not None else build_container(environment, modules=modules)
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=_build_lifespan(resolved))
    setattr(app.state, CONTAINER_STATE_ATTR, resolved)
    app.add_middleware(RequestIdMiddleware)
    # Added after RequestIdMiddleware on purpose: the last add_middleware is the outermost, so
    # CORS wraps RequestIdMiddleware and even the 500 envelopes built inside it carry the CORS
    # headers a browser needs to read them.
    _add_cors(app, environment)
    register_exception_handlers(app)
    for module in resolve_modules(modules):
        app.include_router(module.router, prefix=API_PREFIX)
    return app
