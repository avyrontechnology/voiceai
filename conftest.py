"""Shared fixtures for every test tree (`tests/` and `voiceai/modules/*/tests/`).

Canonical definitions live in `tests/arch/conftest.py`; this file re-exports them so
colocated module tests resolve fixtures by name without importing another tree's
conftest (talko parity — each component's tests stand alone). The re-exported
fixture marks are preserved, so pytest registers them repo-wide from here.
"""

from __future__ import annotations

from tests.arch.conftest import (  # noqa: F401 — re-exported for repo-wide fixture registration
    TEST_BASE_URL,
    FakeRedis,
    arch_app,
    arch_client,
    arch_environment,
    client_factory,
    container_override,
    failing_redis,
    fake_redis,
)
