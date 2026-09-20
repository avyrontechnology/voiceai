"""Process wiring: environment, dependency container, infrastructure clients, app factory."""

from voiceai.core.app_factory import RequestIdMiddleware, create_app
from voiceai.core.container import VoiceAIContainer, aclose_container, build_container
from voiceai.core.db import DatabaseClient, InMemoryDatabase, MotorDatabase, create_db
from voiceai.core.environment import (
    Environment,
    get_environment,
    load_environment,
    reset_environment,
)
from voiceai.core.redis import create_redis, ping_redis

__all__ = [
    "VoiceAIContainer",
    "aclose_container",
    "DatabaseClient",
    "Environment",
    "InMemoryDatabase",
    "MotorDatabase",
    "RequestIdMiddleware",
    "build_container",
    "create_app",
    "create_db",
    "create_redis",
    "get_environment",
    "load_environment",
    "ping_redis",
    "reset_environment",
]
