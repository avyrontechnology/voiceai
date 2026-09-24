"""Process wiring: environment, dependency container, infrastructure clients, app factory."""

from voiceai.core.app_factory import RequestIdMiddleware, TenantMiddleware, create_app
from voiceai.core.container import VoiceAIContainer, aclose_container, build_container
from voiceai.core.db import DatabaseClient, InMemoryDatabase, MotorDatabase, create_db
from voiceai.core.environment import (
    Environment,
    get_environment,
    load_environment,
    reset_environment,
)
from voiceai.core.redis import create_redis, create_redis_cache, ping_redis
from voiceai.core.resilience import TaskRegistry

__all__ = [
    "VoiceAIContainer",
    "aclose_container",
    "DatabaseClient",
    "Environment",
    "InMemoryDatabase",
    "MotorDatabase",
    "RequestIdMiddleware",
    "TaskRegistry",
    "TenantMiddleware",
    "build_container",
    "create_app",
    "create_db",
    "create_redis",
    "create_redis_cache",
    "get_environment",
    "load_environment",
    "ping_redis",
    "reset_environment",
]
