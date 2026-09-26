"""Auth user administration slice: invites, users, password, audit reads (spec 0040).

`AuthAdminMixin` owns the directory-administration use cases: invite issue /
redeem / listing / revocation, user listing, role changes, deletion, password
rotation, and tenant-scoped audit reads. It inherits the shared kernel
(`service_base.AuthServiceBase`) for minting, guards, and audit writes, and
composes into `service.AuthService` — one responsibility per file. Split cited
by spec 0040 (integrator): the identity program grew `service.py` past the
800-line canonical budget.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import ConflictError, InvalidRequestError
from voiceai.common.ids import new_id
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.errors import (
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
)
from voiceai.modules.auth.exceptions import (
    ensure_authenticated,
    ensure_found,
    ensure_invite_valid,
    ensure_permitted,
)
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.user import User, UserRole
from voiceai.modules.auth.service_base import AuthServiceBase, SessionTokens
from voiceai.modules.auth.static_methods import hash_password, new_token, token_hash, verify_password

__all__ = ["AuthAdminMixin"]


class AuthAdminMixin(AuthServiceBase):
    """Directory administration over the shared kernel (mixed into `AuthService`)."""

    # -- invites ------------------------------------------------------------

    async def invite(self, principal: Principal, email: str, name: str | None, role: UserRole) -> tuple[Invite, str]:
        """Create an invite; HYBRID delivery — the raw token returns to the caller.

        Raises:
            ForbiddenError: When the caller is not an admin, or grants
                owner/admin without being owner.
            ConflictError: When the email is already registered.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        if role in ("owner", "admin") and principal.role != "owner":
            raise ForbiddenError("Only owners can invite owners or admins")
        if await self._store.get_user_by_email(email.strip().lower()) is not None:
            raise ConflictError("Email already registered")
        token = new_token()
        invite = Invite(
            invite_id=new_id("inv"),
            email=email.strip().lower(),
            name=name,
            role=role,
            org_id=principal.org_id,
            tenant_id=principal.tenant_id,
            token_hash=token_hash(token),
            expires_at=utc_now() + timedelta(seconds=C.INVITE_TTL_S),
            created_by=principal.user_id,
        )
        await self._store.save_invite(invite)
        await self.audit(
            "invite",
            user_id=principal.user_id,
            email=principal.email,
            detail=f"{invite.email} as {invite.role}",
        )
        return invite, token

    async def accept_invite(self, token: str, name: str | None, password: str) -> tuple[User, SessionTokens]:
        """Redeem an invite token into a user plus a first legacy session and JWT pair.

        Raises:
            InviteInvalidError: When no live invite matches the token.
            ConflictError: When the invite email registered meanwhile.
        """
        digest = token_hash(token)
        matched = await self._store.get_invite_by_token_hash(digest)
        if matched is not None and matched.accepted:
            matched = None
        live: Invite = ensure_invite_valid(matched)
        expires_at = live.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utc_now():
            raise InviteInvalidError("Invite invalid or expired")
        if await self._store.get_user_by_email(live.email) is not None:
            raise ConflictError("Email already registered")
        user = User(
            user_id=new_id("usr"),
            email=live.email,
            name=name or live.name,
            password_hash=hash_password(password),
            role=live.role,
            org_id=live.org_id,
            tenant_id=live.tenant_id,
        )
        await self._store.save_user(user)
        live.accepted = True
        await self._store.save_invite(live)
        legacy = await self._new_session(
            user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S, tenant_id=user.tenant_id
        )
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("invite_accepted", user_id=user.user_id, email=user.email, detail=f"as {user.role}")
        return user, SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def list_invites(self, principal: Principal) -> list[Invite]:
        """Return pending invites, token hashes never leaving the store (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        return [i for i in await self._store.list_invites() if not i.accepted and i.org_id == principal.org_id]

    async def delete_invite(self, principal: Principal, invite_id: str) -> None:
        """Revoke an invite (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
            AuthNotFoundError: When the invite does not exist or belongs to
                another org (no cross-tenant oracle).
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        invite: Invite = ensure_found(await self._store.get_invite(invite_id), "Invite not found")
        self._ensure_same_org(invite.org_id, principal, "Invite")
        await self._store.delete_invite(invite_id)
        await self.audit("invite_revoked", user_id=principal.user_id, email=principal.email, detail=invite_id)

    # -- admin --------------------------------------------------------------

    async def list_users(self, principal: Principal) -> list[User]:
        """Return every user in the caller's org (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        return [u for u in await self._store.list_users() if u.org_id == principal.org_id]

    async def _assert_last_owner_safe(self, target: User) -> None:
        """Reject de-powering the target org's last active owner (shared by role/delete)."""
        if target.role != "owner" or target.disabled:
            return
        owners = [
            u
            for u in await self._store.list_users()
            if u.role == "owner" and not u.disabled and u.org_id == target.org_id
        ]
        if len(owners) <= 1:
            raise InvalidRequestError("Cannot remove the last active owner")

    async def set_user_role(self, principal: Principal, target_id: str, role: UserRole) -> User:
        """Change a user's role, revoking their sessions and access tokens (owners only).

        Raises:
            ForbiddenError: When the caller is not an owner.
            AuthNotFoundError: When the target does not exist or belongs to
                another org (no cross-tenant oracle).
            InvalidRequestError: On self-edit or last-owner removal.
        """
        actor = await self._require_owner(principal)
        target: User = ensure_found(await self._store.get_user(target_id), "User not found")
        self._ensure_same_org(target.org_id, principal, "User")
        if target.user_id == actor.user_id:
            raise InvalidRequestError("Cannot change your own role")
        await self._assert_last_owner_safe(target)
        target.role = role
        await self._bump_version(target)
        await self._store.save_user(target)
        await self._store.delete_user_sessions(target_id)
        await self.audit(
            "role_change",
            user_id=actor.user_id,
            email=actor.email,
            detail=f"{target.email} -> {role}",
        )
        return target

    async def delete_user(self, principal: Principal, target_id: str) -> None:
        """Delete a user plus all their sessions (owners only).

        Raises:
            ForbiddenError: When the caller is not an owner.
            AuthNotFoundError: When the target does not exist or belongs to
                another org (no cross-tenant oracle).
            InvalidRequestError: On self-delete or last-owner removal.
        """
        actor = await self._require_owner(principal)
        target: User = ensure_found(await self._store.get_user(target_id), "User not found")
        self._ensure_same_org(target.org_id, principal, "User")
        if target.user_id == actor.user_id:
            raise InvalidRequestError("Cannot delete yourself")
        await self._assert_last_owner_safe(target)
        await self._store.delete_user_sessions(target_id)
        await self._store.delete_user(target_id)
        await self.audit("user_deleted", user_id=actor.user_id, email=actor.email, detail=target.email)

    async def change_password(
        self,
        principal: Principal,
        current_password: str,
        new_password: str,
    ) -> SessionTokens:
        """Rotate a session caller's password, re-issuing their tokens.

        Unlike the legacy keep-current-session sweep, rotation always re-issues:
        the version bump kills every outstanding access token (including the
        caller's, within its short TTL) and every session row dies, so the caller
        leaves with the only live pair. Strictly stronger, and the only sound
        story once access tokens are stateless.

        Raises:
            ForbiddenError: When the caller has no login session.
            InvalidCredentialsError: When the record vanished or the current
                password mismatches (one gate — no user enumeration).
        """
        ensure_authenticated(principal)
        if principal.auth_type != "session" or not principal.user_id:
            raise ForbiddenError("Password change requires a login session")
        user = await self._store.get_user(principal.user_id)
        if not user or not verify_password(current_password, user.password_hash):
            raise InvalidCredentialsError("Current password is incorrect")
        user.password_hash = hash_password(new_password)
        await self._bump_version(user)
        await self._store.save_user(user)
        await self._store.delete_user_sessions(user.user_id)
        legacy = await self._new_session(
            user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S, tenant_id=user.tenant_id
        )
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("password_change", user_id=user.user_id, email=user.email)
        return SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def auth_events(self, principal: Principal) -> list[AuthEvent]:
        """Return recent audit events in the caller's tenant, newest first (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        return [e for e in await self._store.list_auth_events() if e.tenant_id == principal.tenant_id]
