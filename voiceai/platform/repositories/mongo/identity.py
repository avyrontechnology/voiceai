"""Mongo backend: identity collection group (split from mongo.py; behavior frozen)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Type

from beanie import Document
from pymongo import AsyncMongoClient

from voiceai.core import db as db_factory
from voiceai.platform import models as platform_models
from voiceai.platform.exceptions import ConflictError, InvalidRequestError
from voiceai.platform.models import (
    ApiKey,
    AuthEvent,
    Batch,
    BatchStatus,
    Execution,
    ExecutionStatus,
    GraphDoc,
    GraphVersion,
    InboundConfig,
    Integration,
    Invite,
    KnowledgeBase,
    LedgerEntry,
    Organization,
    PhoneNumber,
    SessionRecord,
    SubAccount,
    Tool,
    User,
    VectorStoreConfig,
    VoiceEntry,
    Wallet,
    Webhook,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDoc,
    WorkflowRun,
    WorkflowVersion,
    new_id,
    utcnow,
)

from voiceai.otobaai_logger import get_logger

logger = get_logger(__name__)


class IdentityMixin:
    """IdentityMixin for MongoStore composition."""

    async def save_api_key(self, key: ApiKey) -> None:
        """Insert or replace an API key (hash only, never the secret).

        Args:
            key: Key to persist.
        """
        await self._upsert(ApiKey, "key_id", key)

    async def get_api_key_by_hash(self, digest: str) -> Optional[ApiKey]:
        """Resolve an API key by hash (unique index, no sweep).

        Args:
            digest: Key hash.

        Returns:
            The key or None.
        """
        doc = await ApiKey.find_one({"key_hash": str(digest)})
        return ApiKey(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_api_keys(self) -> List[ApiKey]:
        """List all API keys.

        Returns:
            The keys.
        """
        return [ApiKey(**self._clean(d.model_dump(mode="json"))) for d in await ApiKey.find_many({}).to_list()]

    async def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key.

        Args:
            key_id: Key identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(ApiKey, "key_id", key_id)

    async def delete_user_api_keys(self, user_id: str) -> int:
        """Revoke every API key owned by a user.

        Args:
            user_id: Owner user id.

        Returns:
            Keys revoked.
        """
        docs = await ApiKey.find_many({"created_by": str(user_id)}).to_list()
        for doc in docs:
            await doc.delete()
        return len(docs)

    async def save_user(self, user: User) -> None:
        """Insert or replace a user by ``user_id``.

        Args:
            user: User to persist.

        Raises:
            DuplicateKeyError: When the email belongs to another user.
        """
        await self._upsert(User, "user_id", user)

    async def get_user(self, user_id: str) -> Optional[User]:
        """Fetch one user.

        Args:
            user_id: User identifier.

        Returns:
            The user or None.
        """
        doc = await self._get(User, "user_id", user_id)
        return User(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Resolve a user by email, case-insensitively.

        Args:
            email: Email address.

        Returns:
            The user or None.
        """
        import re as _re

        needle = email.strip()
        doc = await User.find_one({"email": {"$regex": f"^{_re.escape(needle)}$", "$options": "i"}})
        return User(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_users(self) -> List[User]:
        """List all users.

        Returns:
            The users.
        """
        return [User(**self._clean(d.model_dump(mode="json"))) for d in await User.find_many({}).to_list()]

    async def count_users(self) -> int:
        """Count users.

        Returns:
            The user count.
        """
        return await User.find_many({}).count()

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user.

        Args:
            user_id: User identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(User, "user_id", user_id)

    async def save_session(self, session: SessionRecord) -> None:
        """Insert or replace a session by token hash.

        Args:
            session: Session to persist.
        """
        await self._upsert(SessionRecord, "token_hash", session)

    async def get_session(self, token_hash: str) -> Optional[SessionRecord]:
        """Fetch a session, reaping it when expired (mirrors MemoryStore).

        Args:
            token_hash: Token hash.

        Returns:
            The live session or None.
        """
        doc = await SessionRecord.find_one({"token_hash": str(token_hash)})
        if doc is None:
            return None
        session = SessionRecord(**self._clean(doc.model_dump(mode="json")))
        if session.expires_at.tzinfo is None:
            valid = session.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
        else:
            valid = session.expires_at > datetime.now(timezone.utc)
        if not valid:
            await doc.delete()
            return None
        return session

    async def delete_session(self, token_hash: str) -> bool:
        """Delete a session.

        Args:
            token_hash: Token hash.

        Returns:
            True when a row existed.
        """
        return await self._delete(SessionRecord, "token_hash", token_hash)

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete all of a user's sessions.

        Args:
            user_id: Owner user id.

        Returns:
            Sessions removed.
        """
        docs = await SessionRecord.find_many({"user_id": str(user_id)}).to_list()
        for doc in docs:
            await doc.delete()
        return len(docs)

    async def delete_user_sessions_except(self, user_id: str, keep: set) -> int:
        """Delete all of a user's sessions except ``keep``.

        Args:
            user_id: Owner user id.
            keep: Token hashes to preserve.

        Returns:
            Sessions removed.
        """
        keep = set(keep or set())
        docs = await SessionRecord.find_many({"user_id": str(user_id)}).to_list()
        doomed = [d for d in docs if d.token_hash not in keep]
        for doc in doomed:
            await doc.delete()
        return len(doomed)

    async def save_invite(self, invite: Invite) -> None:
        """Insert or replace an invite.

        Args:
            invite: Invite to persist.
        """
        await self._upsert(Invite, "invite_id", invite)

    async def get_invite(self, invite_id: str) -> Optional[Invite]:
        """Fetch one invite.

        Args:
            invite_id: Invite identifier.

        Returns:
            The invite or None.
        """
        doc = await self._get(Invite, "invite_id", invite_id)
        return Invite(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def get_invite_by_token_hash(self, digest: str) -> Optional[Invite]:
        """Resolve an invite by token hash (unique index, no sweep).

        Args:
            digest: Token hash.

        Returns:
            The invite or None.
        """
        doc = await Invite.find_one({"token_hash": str(digest)})
        return Invite(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_invites(self) -> List[Invite]:
        """List all invites.

        Returns:
            The invites.
        """
        return [Invite(**self._clean(d.model_dump(mode="json"))) for d in await Invite.find_many({}).to_list()]

    async def delete_invite(self, invite_id: str) -> bool:
        """Delete an invite.

        Args:
            invite_id: Invite identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(Invite, "invite_id", invite_id)

    async def add_auth_event(self, event: AuthEvent) -> None:
        """Append an auth audit event.

        Args:
            event: Event to persist.
        """
        await self._upsert(AuthEvent, "event_id", event)

    async def list_auth_events(self, limit: int = 100) -> List[AuthEvent]:
        """List auth events newest-first.

        Args:
            limit: Max events.

        Returns:
            The events.
        """
        items = [AuthEvent(**self._clean(d.model_dump(mode="json"))) for d in await AuthEvent.find_many({}).to_list()]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]
