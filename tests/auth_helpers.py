"""Shared auth setup for platform contract tests.

Existing endpoint tests predate enforcement; their fixtures call
signup_owner() so requests carry an owner session cookie. Since spec 0006 E4
the legacy `/auth` routes are dark — signup rides the cut-over controller at
`/api/v1/auth` (enveloped), mounted per-app by mount_new_auth().
"""

OWNER_EMAIL = "owner@acme.test"
OWNER_PASSWORD = "correct-horse-1"


def mount_new_auth(app):
    """Serve the cut-over auth controller on a platform test app (spec 0006 E4).

    Binds the app's own store in a fresh container (mirrors production wiring)
    so the controller resolves its service without touching the legacy seam.
    """
    from dependency_injector import providers

    from voiceai.common.responses import register_exception_handlers
    from voiceai.core.container import build_container
    from voiceai.core.environment import Environment
    from voiceai.modules import auth as auth_module
    from voiceai.modules.wallet.adapters.legacy_store import build_legacy_wallet_service

    register_exception_handlers(app)  # why: the cut-over controller raises AppError; the factory owns rendering
    container = build_container(Environment())
    container.auth_store.override(providers.Object(app.state.platform_store))
    container.wallet_service.override(providers.Factory(build_legacy_wallet_service, app.state.platform_store))
    app.state.container = container
    app.include_router(auth_module.MODULE.router, prefix="/api/v1")
    return app


async def signup_owner(client, email: str = OWNER_EMAIL):
    """Register the first (owner) user on a fresh app. Asserts success."""
    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "name": "Owner", "password": OWNER_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["role"] == "owner"
    return data
