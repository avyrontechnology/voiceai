"""Mongo-backed user repository: CRUD + version bump behind the user port (spec 0064, ISP).

`MongoUserRepo` owns the `users` collection — the only code that touches user
rows directly. The auth service delegates every user read/write here, so the
repository is the single seam for user persistence. Natural keys pin `id`
(`user_id`), so identity is unchanged from the legacy stores; deletes are soft
(AGENTS.md rule 5) where the legacy stores destroy.
"""

from __future__ import annotations

from voiceai.database.base import BaseFields
from voiceai.database.repository import BaseRepository
from voiceai.modules.auth.models.user import User

__all__ = ["MongoUserRepo"]


def _pin(model: BaseFields, natural_id: str) -> BaseFields:
    """Pin a model's storage id to its natural key, returning it for chaining."""
    model.id = natural_id
    return model


class MongoUserRepo:
    """User persistence over one `BaseRepository[User]` (Atlas in prod, in-memory in tests).

    Args:
        users: The `users` collection (`email` unique index, see `scripts/migrate.py`).
    """

    def __init__(self, users: BaseRepository[User]) -> None:
        self._users = users

    async def get_by_id(self, user_id: str) -> User | None:
        """Return the active user with this id, or `None`."""
        return await self._users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Return the active user with this email, or `None` (indexed, no scan)."""
        return await self._users.find_one("email", email.strip().lower())

    async def create(self, user: User) -> User:
        """Persist a new user keyed by `user_id`."""
        _pin(user, user.user_id)
        await self._users.insert(user)
        return user

    async def update_version(self, user: User) -> User:
        """Bump the user's `token_version` and persist the row.

        Legacy platform rows (the retired Redis store, spec 0048) predate the
        stamp: stamping is skipped for them while session sweeps keep revoking,
        so a mixed deployment degrades to sweep-only revocation instead of crashing.
        """
        if "token_version" in type(user).model_fields:
            version = getattr(user, "token_version", 0)
            user.token_version = (version if isinstance(version, int) else 0) + 1
        _pin(user, user.user_id)
        await self._users.insert(user)
        return user
