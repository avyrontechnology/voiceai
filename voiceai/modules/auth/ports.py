"""Store port the auth service depends on (AGENTS.md rule 9).

``AuthStorePort`` names exactly the store methods the auth domain touches; both legacy
stores (``MemoryStore`` today, ``RedisStore`` in production — a subclass of the
former) satisfy it structurally without importing this package. Model payloads are
typed ``Any`` at C0 on purpose: the auth models still live in ``platform/models.py``
(which a non-adapter module file may not import), and step C2 re-points every
payload to the moved models when they land in ``models/``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["AuthStorePort"]


@runtime_checkable
class AuthStorePort(Protocol):
    """Persistence the auth service needs — users, sessions, invites, keys, audit."""

    async def save_user(self, user: Any) -> None:  # why: User lands in models/ at C2
        """Persist a user (insert or replace)."""
        ...

    async def get_user(self, user_id: str) -> Any | None:  # why: User lands in models/ at C2
        """Return the user with this id, or `None`."""
        ...

    async def get_user_by_email(self, email: str) -> Any | None:  # why: User lands in models/ at C2
        """Return the user with this email, or `None`."""
        ...

    async def list_users(self) -> list[Any]:  # why: User lands in models/ at C2
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

    async def save_session(self, session: Any) -> None:  # why: SessionRecord lands in models/ at C2
        """Persist a session record."""
        ...

    async def get_session(self, token_hash: str) -> Any | None:  # why: SessionRecord lands in models/ at C2
        """Return the session for a token hash, or `None`."""
        ...

    async def delete_session(self, token_hash: str) -> bool:
        """Delete a session; `True` when one existed."""
        ...

    async def save_invite(self, invite: Any) -> None:  # why: Invite lands in models/ at C2
        """Persist an invite."""
        ...

    async def get_invite(self, invite_id: str) -> Any | None:  # why: Invite lands in models/ at C2
        """Return the invite with this id, or `None`."""
        ...

    async def list_invites(self) -> list[Any]:  # why: Invite lands in models/ at C2
        """Return every invite."""
        ...

    async def delete_invite(self, invite_id: str) -> bool:
        """Delete an invite; `True` when one existed."""
        ...

    async def list_api_keys(self) -> list[Any]:  # why: ApiKey lands in models/ at C2
        """Return every API key."""
        ...

    async def save_api_key(self, key: Any) -> None:  # why: ApiKey lands in models/ at C2
        """Persist an API key."""
        ...

    async def add_auth_event(self, event: Any) -> None:  # why: AuthEvent lands in models/ at C2
        """Append one audit event."""
        ...

    async def list_auth_events(self, limit: int = 100) -> list[Any]:  # why: AuthEvent lands in models/ at C2
        """Return recent audit events, newest first."""
        ...
