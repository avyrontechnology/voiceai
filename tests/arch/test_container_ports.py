"""Container port resolution tests (spec 0067)."""

from __future__ import annotations

from voiceai.core.container import VoiceAIContainer
from voiceai.core.ports import SessionStore, TokenService, UserRepo


def test_container_resolves_auth_service() -> None:
    """AuthService is resolvable through the container."""
    container = VoiceAIContainer()
    service = container.auth_service()
    assert service is not None


def test_container_resolves_token_service() -> None:
    """TokenService is resolvable through the container."""
    container = VoiceAIContainer()
    tokens = container.token_service()
    assert tokens is not None


def test_container_resolves_user_repo() -> None:
    """UserRepo is resolvable through the container."""
    container = VoiceAIContainer()
    users = container.user_repo()
    assert users is not None


def test_ports_are_importable() -> None:
    """Ports are importable from core.ports."""
    from voiceai.core.ports import SessionStore as SS
    from voiceai.core.ports import TokenService as TS
    from voiceai.core.ports import UserRepo as UR

    assert SS is not None
    assert TS is not None
    assert UR is not None
