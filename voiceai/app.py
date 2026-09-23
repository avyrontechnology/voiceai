"""Production application entry: the greenfield base, without quickstart (T0).

This is the single process entry for deployments (the talko `src/main.py` shape):
environment → container → app, composed once at import. Run it with::

    uvicorn voiceai.app:app --host 0.0.0.0 --port 5001

Composition lives in :func:`voiceai.core.container.build_container` and
:func:`voiceai.core.app_factory.create_app`; this module only orders the calls so
the entry stays thin and every behavior stays tested behind the factories.
"""

from __future__ import annotations

from fastapi import FastAPI

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.environment import load_environment

__all__ = ["app", "build_app"]


def build_app() -> FastAPI:
    """Compose the production application from the process environment.

    Resolves the database client eagerly so driver/config failures (missing
    `motor`, unknown backend, `mongo` without a URL) crash the boot with a clear
    log instead of 500ing every request — including liveness. Connection failures
    stay lazy: an unreachable database degrades to 503s, never a dead boot.

    Returns:
        The fully wired FastAPI application.

    Raises:
        ConfigurationError: When the environment is invalid or the selected
            database backend cannot be constructed.
    """
    environment = load_environment()
    container = build_container(environment)
    container.db_client()
    return create_app(env=environment, container=container)


app = build_app()
