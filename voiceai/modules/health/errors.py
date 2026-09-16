"""Error types raised by the health module (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.common.errors import DependencyUnavailableError


class HealthCheckError(DependencyUnavailableError):
    """Raised when a readiness probe finds a dependency the service cannot work without.

    Inherits the dependency semantics: HTTP 503 and ``retryable=True``, so a load balancer or
    orchestrator knows to keep the instance out of rotation and try again rather than fail hard.
    """
