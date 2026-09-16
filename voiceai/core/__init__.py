"""Process wiring: environment, dependency container, infrastructure clients, app factory."""

from voiceai.core.app_factory import RequestIdMiddleware, create_app
from voiceai.core.container import Container, build_container, get_container
from voiceai.core.db import DatabaseClient, InMemoryDatabase, create_db
from voiceai.core.environment import (
    Environment,
    get_environment,
    load_environment,
    reset_environment,
)
from voiceai.core.redis import create_redis, ping_redis

__all__ = [
    "Container",
    "DatabaseClient",
    "Environment",
    "InMemoryDatabase",
    "RequestIdMiddleware",
    "build_container",
    "create_app",
    "create_db",
    "create_redis",
    "get_container",
    "get_environment",
    "load_environment",
    "ping_redis",
    "reset_environment",
]
