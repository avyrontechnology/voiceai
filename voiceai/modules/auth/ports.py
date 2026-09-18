"""Store port the auth service depends on (AGENTS.md rule 9).

``AuthStorePort`` names exactly the store methods the auth domain touches; both legacy
stores (``MemoryStore`` today, ``RedisStore`` in production — a subclass of the
former) satisfy it structurally without importing this package.
"""

from __future__ import annotations

from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User

__all__ = ["AuthStorePort"]

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthStorePort(Protocol):
    """Persistence the auth service needs — users, sessions, invites, keys, audit."""

    async def save_user(self, user: User) -> None:
        """Persist a user (insert or replace)."""
        ...

    async def get_user(self, user_id: str) -> User | None:
        """Return the user with this id, or `None`."""
        ...

    async def get_user_by_email(self, email: str) -> User | None:
        """Return the user with this email, or `None`."""
        ...

    async def list_users(self) -> list[User]:
        """Return every user."""
        ...

    async def count_users(self) -> int:
        """Return the number of users."""
        ...

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user; `True` when one existed."""
        ...

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete every session of a user; return the count."""
        ...

    async def save_session(self, session: SessionRecord) -> None:
        """Persist a session record."""
        ...

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        """Return the session for a token hash, or `None`."""
        ...

    async def delete_session(self, token_hash: str) -> bool:
        """Delete a session; `True` when one existed."""
        ...

    async def save_invite(self, invite: Invite) -> None:
        """Persist an invite."""
        ...

    async def get_invite(self, invite_id: str) -> Invite | None:
        """Return the invite with this id, or `None`."""
        ...

    async def list_invites(self) -> list[Invite]:
        """Return every invite."""
        ...

    async def delete_invite(self, invite_id: str) -> bool:
        """Delete an invite; `True` when one existed."""
        ...

    async def list_api_keys(self) -> list[ApiKey]:
        """Return every API key."""
        ...

    async def save_api_key(self, key: ApiKey) -> None:
        """Persist an API key."""
        ...

    async def add_auth_event(self, event: AuthEvent) -> None:
        """Append one audit event."""
        ...

    async def list_auth_events(self, limit: int = 100) -> list[AuthEvent]:
        """Return recent audit events, newest first."""
        ...

    async def list_user_sessions(self, user_id: str) -> list[SessionRecord]:
        """Return every session record of a user (password-change sweep)."""
        ...
