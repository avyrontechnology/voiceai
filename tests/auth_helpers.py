"""Shared setup for the platform contract tests: the single app, offline (spec 0048).

The frozen platform routers ride `create_app` over an in-memory container; signup
rides the greenfield auth module, which needs signing keys to mint its token pair,
so the offline environment gets the test-only RS256 settings.
"""

from __future__ import annotations

OWNER_EMAIL = "owner@acme.test"
OWNER_PASSWORD = "correct-horse-1"


def build_platform_test_app():
    """Build the single app over an offline container with signing keys for signup."""
    from dependency_injector import providers

    from voiceai.core.app_factory import create_app
    from voiceai.core.container import build_container
    from voiceai.core.environment import Environment
    from voiceai.modules.auth.service import AuthService
    from voiceai.modules.auth.tests.conftest import _JWT

    environment = Environment(
        app_env="dev",
        log_level="INFO",
        redis_url="",
        db_backend="memory",
        db_url="",
        db_name="otoba_test",
        allowed_origins=(),
        cookie_secure=None,
    )
    container = build_container(environment)
    container.auth_service.override(providers.Object(AuthService(container.auth_store(), jwt=_JWT)))
    return create_app(env=environment, container=container)


async def signup_owner(client, email: str = OWNER_EMAIL):
    """Register the first (owner) user on a fresh app. Asserts success; returns the user."""
    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "name": "Owner", "password": OWNER_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["user"]["role"] == "owner"
    return data["user"]
