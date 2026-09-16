"""Health module: probes this service's dependencies and exposes them over HTTP."""

from __future__ import annotations

from typing import TYPE_CHECKING

from voiceai.common.constants import CONTAINER_KEY_DB, CONTAINER_KEY_REDIS
from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.modules import ModuleDef
from voiceai.modules.health.constants import MODULE_NAME
from voiceai.modules.health.controller import router
from voiceai.modules.health.repository import HealthRepository
from voiceai.modules.health.service import HealthService

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container


def register(container: Container) -> None:
    """Bind this module's repository and service into a container (AGENTS.md rule 9).

    Providers are lazy, so the clients they depend on can still be replaced (a test swapping a
    fake redis in, for example) between building the container and the first request.

    Args:
        container: The container being composed, already carrying the core infrastructure.
    """
    started_at = utc_now()

    def build_repository(scope: Container) -> HealthRepository:
        """Build the repository from the infrastructure clients core registered."""
        return HealthRepository(scope.resolve(CONTAINER_KEY_REDIS), scope.resolve(CONTAINER_KEY_DB))

    def build_service(scope: Container) -> HealthService:
        """Build the service from the repository, the project logger, and the start time."""
        return HealthService(scope.resolve(HealthRepository), get_logger(MODULE_NAME), started_at)

    container.register(HealthRepository, build_repository)
    container.register(HealthService, build_service)


MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router, register=register)

__all__ = ["MODULE", "register"]
