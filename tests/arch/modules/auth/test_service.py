"""Auth service behavior at its new home (spec 0005, C3).

Every test drives the REAL `AuthService` against a dict-backed fake store behind
`AuthStorePort` (rule 9 — DI fakes through the constructor), pinning the
verbatim-legacy checklist: first-user-owner signup + closed + dup gates, login
ttl selection + failure audit, invite hybrid token + accept + expiry, owner
admin with last-owner protection, password rotation keeping only the current
session, ticket mint/redeem single-use, and audit that never fails the op.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from voiceai.common.errors import ConflictError, InvalidRequestError
from voiceai.modules.auth.errors import (
    AuthNotFoundError,
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
    TooManyAttemptsError,
)
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.static_methods import hash_password, new_token, token_hash


class FakeAuthStore:
    """Dict-backed `AuthStorePort` with MemoryStore session-expiry semantics."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.invites: dict[str, Invite] = {}
        self.keys: dict[str, ApiKey] = {}
        self.events: list[AuthEvent] = []

    async def save_user(self, user: User) -> None:
        self.users[user.user_id] = user

    async def get_user(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    async def list_users(self) -> list[User]:
        return list(self.users.values())

    async def count_users(self) -> int:
        return len(self.users)

    async def delete_user(self, user_id: str) -> bool:
        return self.users.pop(user_id, None) is not None

    async def delete_user_sessions(self, user_id: str) -> int:
        doomed = [h for h, s in self.sessions.items() if s.user_id == user_id]
        for h in doomed:
            del self.sessions[h]
        return len(doomed)

    async def save_session(self, session: SessionRecord) -> None:
        self.sessions[session.token_hash] = session

    async def get_session(self, token_hash_: str) -> SessionRecord | None:
        session = self.sessions.get(token_hash_)
        if session is None:
            return None
        expires_at = session.expires_at
        valid = (
            expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
            if expires_at.tzinfo is None
            else expires_at > datetime.now(timezone.utc)
        )
        if not valid:
            del self.sessions[token_hash_]
            return None
        return session

    async def delete_session(self, token_hash_: str) -> bool:
        return self.sessions.pop(token_hash_, None) is not None

    async def save_invite(self, invite: Invite) -> None:
        self.invites[invite.invite_id] = invite

    async def get_invite(self, invite_id: str) -> Invite | None:
        return self.invites.get(invite_id)

    async def list_invites(self) -> list[Invite]:
        return list(self.invites.values())

    async def delete_invite(self, invite_id: str) -> bool:
        return self.invites.pop(invite_id, None) is not None

    async def list_api_keys(self) -> list[ApiKey]:
        return list(self.keys.values())

    async def save_api_key(self, key: ApiKey) -> None:
        self.keys[key.key_id] = key

    async def add_auth_event(self, event: AuthEvent) -> None:
        self.events.append(event)

    async def list_auth_events(self, limit: int = 100) -> list[AuthEvent]:
        return list(reversed(self.events[-limit:]))

    async def list_user_sessions(self, user_id: str) -> list[SessionRecord]:
        return [s for s in self.sessions.values() if s.user_id == user_id]


def _owner_principal(user: User) -> Principal:
    """Act-as principal the way the session resolver builds one."""
    return Principal(
        user_id=user.user_id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        auth_type="session",
    )


async def _signed_up(service: AuthService, email: str = "owner@x.test") -> tuple[User, str]:
    """First-user signup shortcut (owner + raw session token)."""
    return await service.signup(email, "Owner", "owner-pass-1")


# -- signup / login ---------------------------------------------------------


async def test_signup_mints_owner_with_live_session() -> None:
    """First signup yields an owner whose token authenticates."""
    service = AuthService(FakeAuthStore())

    user, token = await _signed_up(service)

    assert user.role == "owner"
    principal = await service.authenticate(token, None)
    assert principal.user_id == user.user_id


async def test_signup_closed_after_first_user() -> None:
    """Second signup is forbidden even with a fresh email."""
    service = AuthService(FakeAuthStore())
    await _signed_up(service)

    with pytest.raises(ForbiddenError, match="Signup is closed"):
        await service.signup("second@x.test", "Second", "second-pass-1")


async def test_signup_closed_gate_fires_before_duplicate_check() -> None:
    """Any existing user closes signup — even for a taken email (verbatim order)."""
    store = FakeAuthStore()
    service = AuthService(store)
    await store.save_user(User(user_id="u1", email="taken@x.test", password_hash=hash_password("x"), role="owner"))

    with pytest.raises(ForbiddenError, match="Signup is closed"):
        await service.signup("Taken@X.test", "Dup", "dup-pass-1")


async def test_login_stamps_last_login_and_honors_remember_ttl() -> None:
    """Login returns a live token and records the login instant."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, _ = await _signed_up(service)

    same, token = await service.login(user.email, "owner-pass-1", True, client_ip="10.0.0.1")

    assert same.user_id == user.user_id
    assert store.users[user.user_id].last_login_at is not None
    assert (await service.authenticate(token, None)).user_id == user.user_id


async def test_login_rejects_bad_password_disabled_and_unknown() -> None:
    """Wrong password, disabled flag, and unknown email all read 401."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, _ = await _signed_up(service)

    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        await service.login(user.email, "wrong-pass", False, client_ip="10.0.0.2")
    user.disabled = True
    await store.save_user(user)
    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        await service.login(user.email, "owner-pass-1", False, client_ip="10.0.0.3")
    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        await service.login("ghost@x.test", "whatever-1", False, client_ip="10.0.0.4")
    assert [e.type for e in store.events].count("login_failed") == 3


async def test_login_throttle_trips_after_five_per_ip() -> None:
    """Sixth attempt from one IP reads 429 even with right credentials."""
    service = AuthService(FakeAuthStore())
    user, _ = await _signed_up(service)
    ip = "10.9.9.9"

    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            await service.login(user.email, "wrong-pass", False, client_ip=ip)
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        await service.login(user.email, "owner-pass-1", False, client_ip=ip)


async def test_logout_revokes_token_and_audits() -> None:
    """Logout kills the session; the token stops authenticating."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, token = await _signed_up(service)

    await service.logout(token, _owner_principal(user))

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(token, None)
    assert store.events[-1].type == "logout"


async def test_authenticate_prefers_session_then_bearer() -> None:
    """Session wins; bearer resolves keys; garbage reads 401."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, token = await _signed_up(service)
    secret = new_token()
    store.keys["k1"] = ApiKey(
        key_id="k1",
        name="ci",
        prefix=secret[:8],
        key_hash=token_hash(secret),
        scopes=["calls:write"],
        created_by=user.user_id,
    )

    assert (await service.authenticate(token, f"Bearer {secret}")).user_id == user.user_id
    key_principal = await service.authenticate(None, f"Bearer {secret}")
    assert key_principal.auth_type == "key"
    assert key_principal.key_id == "k1"
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, None)
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, "Bearer not-a-key")


# -- invites ----------------------------------------------------------------


async def test_invite_accept_round_trip() -> None:
    """Owner invites → token redeems into a member with a live session."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)

    invite, raw = await service.invite(_owner_principal(owner), "new@x.test", "New", "member")
    assert invite.accepted is False

    user, session_token = await service.accept_invite(raw, None, "new-pass-1")

    assert user.role == "member" and user.email == "new@x.test"
    assert (await service.authenticate(session_token, None)).user_id == user.user_id
    assert store.invites[invite.invite_id].accepted is True
    with pytest.raises(InviteInvalidError):
        await service.accept_invite(raw, None, "again-pass-1")


async def test_accept_rejects_expired_invite() -> None:
    """Past-expires invites read invalid even with the right token."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    invite, raw = await service.invite(_owner_principal(owner), "old@x.test", None, "member")
    invite.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await store.save_invite(invite)

    with pytest.raises(InviteInvalidError, match="invalid or expired"):
        await service.accept_invite(raw, None, "old-pass-1")


async def test_invite_guards_roles_and_duplicates() -> None:
    """Non-admins cannot invite; admins cannot crown owners; dup emails collide."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, owner_token = await _signed_up(service)
    _, member_token = await service.accept_invite(
        (await service.invite(_owner_principal(owner), "m@x.test", None, "member"))[1],
        None,
        "m-pass-1",
    )
    member = await store.get_user_by_email("m@x.test")
    assert member is not None

    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.invite(await service.authenticate(member_token, None), "z@x.test", None, "member")
    admin = await store.get_user_by_email("m@x.test")
    assert admin is not None
    admin.role = "admin"
    await store.save_user(admin)
    with pytest.raises(ForbiddenError, match="Only owners"):
        await service.invite(_owner_principal(admin), "crown@x.test", None, "owner")
    with pytest.raises(ConflictError, match="already registered"):
        await service.invite(_owner_principal(owner), "m@x.test", None, "member")
    # The owner token still authenticates (unused name keeps mypy honest).
    assert (await service.authenticate(owner_token, None)).user_id == owner.user_id


async def test_list_and_revoke_invites_are_admin_only() -> None:
    """Pending-only listing plus revocation; members are refused."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    owner_p = _owner_principal(owner)
    invite, raw = await service.invite(owner_p, "p@x.test", None, "member")
    await service.accept_invite(raw, None, "p-pass-1")
    pending, _ = await service.invite(owner_p, "q@x.test", None, "viewer")

    listed = await service.list_invites(owner_p)

    assert [i.invite_id for i in listed] == [pending.invite_id]
    await service.delete_invite(owner_p, pending.invite_id)
    assert await service.list_invites(owner_p) == []
    with pytest.raises(AuthNotFoundError, match="Invite not found"):
        await service.delete_invite(owner_p, pending.invite_id)
    assert invite.accepted is True


# -- admin ------------------------------------------------------------------


async def test_set_role_revokes_sessions_and_protects_last_owner() -> None:
    """Role change kills the target's sessions; last owner is untouchable."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    owner_p = _owner_principal(owner)
    invite, raw = await service.invite(owner_p, "a@x.test", None, "member")
    member, member_token = await service.accept_invite(raw, None, "a-pass-1")

    changed = await service.set_user_role(owner_p, member.user_id, "admin")

    assert changed.role == "admin"
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(member_token, None)
    with pytest.raises(InvalidRequestError, match="your own role"):
        await service.set_user_role(owner_p, owner.user_id, "admin")
    with pytest.raises(AuthNotFoundError, match="User not found"):
        await service.set_user_role(owner_p, "nope", "viewer")
    # Last-owner guard (unreachable via self-editing API — pinned directly).
    with pytest.raises(InvalidRequestError, match="last active owner"):
        await service._assert_last_owner_safe(owner)
    assert invite.accepted is True


async def test_owner_gate_rejects_members() -> None:
    """Member principals read 403 on every owner-only call."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    _, raw = await service.invite(_owner_principal(owner), "b@x.test", None, "member")
    _, member_token = await service.accept_invite(raw, None, "b-pass-1")
    member_p = await service.authenticate(member_token, None)

    with pytest.raises(ForbiddenError, match="Requires owner"):
        await service.set_user_role(member_p, owner.user_id, "viewer")
    with pytest.raises(ForbiddenError, match="Requires owner"):
        await service.delete_user(member_p, owner.user_id)
    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.list_users(member_p)
    assert {u.email for u in await service.list_users(_owner_principal(owner))} == {
        owner.email,
        "b@x.test",
    }


async def test_delete_user_removes_record_and_sessions() -> None:
    """Delete drops the user row plus sessions; self-delete is refused."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    owner_p = _owner_principal(owner)
    _, raw = await service.invite(owner_p, "gone@x.test", None, "member")
    gone, gone_token = await service.accept_invite(raw, None, "gone-pass-1")

    await service.delete_user(owner_p, gone.user_id)

    assert await store.get_user(gone.user_id) is None
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(gone_token, None)
    with pytest.raises(InvalidRequestError, match="delete yourself"):
        await service.delete_user(owner_p, owner.user_id)


# -- password / tickets / audit ---------------------------------------------


async def test_change_password_keeps_only_current_session() -> None:
    """Rotation kills sibling sessions; wrong current reads 401-shaped."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, token_a = await _signed_up(service)
    _, token_b = await service.login(user.email, "owner-pass-1", False, client_ip="10.1.1.1")

    await service.change_password(await service.authenticate(token_a, None), "owner-pass-1", "brand-new-1", token_a)

    assert (await service.authenticate(token_a, None)).user_id == user.user_id
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(token_b, None)
    _, token_c = await service.login(user.email, "brand-new-1", False, client_ip="10.1.1.2")
    assert (await service.authenticate(token_c, None)).user_id == user.user_id
    with pytest.raises(InvalidCredentialsError, match="Current password is incorrect"):
        await service.change_password(await service.authenticate(token_a, None), "stale-pass", "x-new-1", token_a)


async def test_change_password_requires_login_session() -> None:
    """API-key callers read 403 on password change."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, _ = await _signed_up(service)
    key_p = Principal(
        user_id=user.user_id,
        email=user.email,
        org_id=user.org_id,
        role="viewer",
        auth_type="key",
        scopes=["*"],
    )

    with pytest.raises(ForbiddenError, match="login session"):
        await service.change_password(key_p, "owner-pass-1", "new-pass-1", None)


async def test_ticket_mint_redeem_is_single_use() -> None:
    """Ticket redeems once to a session principal; tickets never authenticate."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, owner_token = await _signed_up(service)
    owner_p = await service.authenticate(owner_token, None)

    ticket = await service.mint_ticket(owner_p)

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(ticket, None)
    first = await service.redeem_ticket(ticket)
    assert first is not None and first.user_id == owner.user_id
    assert await service.redeem_ticket(ticket) is None
    assert await service.redeem_ticket(None) is None
    assert await service.redeem_ticket("bogus") is None


async def test_ticket_needs_calls_write_scope() -> None:
    """Viewers read 403 on ticket mint."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    _, raw = await service.invite(_owner_principal(owner), "v@x.test", None, "viewer")
    _, viewer_token = await service.accept_invite(raw, None, "v-pass-1")

    with pytest.raises(ForbiddenError, match="Requires calls:write scope"):
        await service.mint_ticket(await service.authenticate(viewer_token, None))


async def test_auth_events_are_admin_only() -> None:
    """Audit listing needs admin; entries arrive newest-first."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    _, raw = await service.invite(_owner_principal(owner), "w@x.test", None, "member")
    _, member_token = await service.accept_invite(raw, None, "w-pass-1")

    events = await service.auth_events(_owner_principal(owner))

    assert events[0].created_at >= events[-1].created_at
    assert {e.type for e in events} >= {"signup", "invite", "invite_accepted"}
    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.auth_events(await service.authenticate(member_token, None))


async def test_audit_never_fails_the_operation() -> None:
    """A broken audit sink still lets signup succeed."""

    class BrokenAudit(FakeAuthStore):
        async def add_auth_event(self, event: AuthEvent) -> None:
            raise RuntimeError("sink down")

    user, token = await _signed_up(AuthService(BrokenAudit()))

    assert user.role == "owner" and token


# -- key / ticket / race edges ------------------------------------------------


async def test_api_key_resolution_edge_cases() -> None:
    """Legacy unhashed keys skip, prefix must match, expiry and disabled refuse."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, _ = await _signed_up(service)
    store.keys["legacy"] = ApiKey(key_id="legacy", name="old", prefix="", key_hash=None, created_by=user.user_id)
    secret = new_token()
    store.keys["good"] = ApiKey(
        key_id="good",
        name="ci",
        prefix=secret[:8],
        key_hash=token_hash(secret),
        scopes=["calls:write"],
        created_by=user.user_id,
    )
    stale_secret = new_token()
    store.keys["stale"] = ApiKey(
        key_id="stale",
        name="old-ci",
        prefix=stale_secret[:8],
        key_hash=token_hash(stale_secret),
        created_by=user.user_id,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert (await service.authenticate(None, f"Bearer {secret}")).key_id == "good"
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, f"Bearer {secret[:4]}XXXX")
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, f"Bearer {stale_secret}")
    user.disabled = True
    await store.save_user(user)
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, f"Bearer {secret}")


async def test_api_key_last_used_failure_still_authenticates() -> None:
    """A failing last-used touch warns but never fails auth."""

    class FlakyKeys(FakeAuthStore):
        async def save_api_key(self, key: ApiKey) -> None:
            if key.last_used_at is not None:
                raise RuntimeError("touch down")
            await super().save_api_key(key)

    store = FlakyKeys()
    service = AuthService(store)
    user, _ = await _signed_up(service)
    secret = new_token()
    await store.save_api_key(
        ApiKey(key_id="f", name="flaky", prefix="", key_hash=token_hash(secret), created_by=user.user_id)
    )

    assert (await service.authenticate(None, f"Bearer {secret}")).key_id == "f"


async def test_redeem_rejects_session_tokens_and_expired_tickets() -> None:
    """Only live ws-tickets redeem; sessions and expired rows read None."""

    class NoExpiryFake(FakeAuthStore):
        async def get_session(self, token_hash_: str) -> SessionRecord | None:
            return self.sessions.get(token_hash_)

    store = NoExpiryFake()
    service = AuthService(store)
    owner, session_token = await _signed_up(service)

    assert await service.redeem_ticket(session_token) is None
    ticket = await service.mint_ticket(_owner_principal(owner))
    store.sessions[token_hash(ticket)].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await service.redeem_ticket(ticket) is None


async def test_accept_invite_collides_when_email_registered_meanwhile() -> None:
    """Invite email taken between issue and redeem reads 409."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    _, raw = await service.invite(_owner_principal(owner), "race@x.test", None, "member")
    await store.save_user(
        User(
            user_id="usr_race",
            email="race@x.test",
            password_hash=hash_password("race-pass-1"),
            role="member",
        )
    )

    with pytest.raises(ConflictError, match="already registered"):
        await service.accept_invite(raw, None, "race-pass-2")


async def test_dead_sessions_and_empty_bearer_read_401() -> None:
    """Tokens of deleted users stop working; an empty bearer is no credential."""
    store = FakeAuthStore()
    service = AuthService(store)
    user, token = await _signed_up(service)
    await store.delete_user(user.user_id)

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(token, None)
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, "Bearer ")
    assert await service._principal_from_api_key(None) is None


async def test_naive_datetimes_from_redis_style_rows_still_compare() -> None:
    """Tz-naive expiry rows (Redis wire shape) take the fixup branch, not a crash."""

    class NoExpiryFake(FakeAuthStore):
        async def get_session(self, token_hash_: str) -> SessionRecord | None:
            return self.sessions.get(token_hash_)

    store = NoExpiryFake()
    service = AuthService(store)
    owner, _ = await _signed_up(service)
    owner_p = _owner_principal(owner)
    ticket = await service.mint_ticket(owner_p)
    naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    store.sessions[token_hash(ticket)].expires_at = naive_utc - timedelta(seconds=60)
    invite, raw = await service.invite(owner_p, "n@x.test", None, "member")
    invite.expires_at = naive_utc + timedelta(seconds=60)
    await store.save_invite(invite)

    assert await service.redeem_ticket(ticket) is None
    nailed, _ = await service.accept_invite(raw, None, "n-pass-1")
    assert nailed.email == "n@x.test"
    await store.delete_user(owner.user_id)
    assert await service.redeem_ticket(await service.mint_ticket(owner_p)) is None


async def test_logout_without_principal_still_revokes() -> None:
    """Anonymous logout revokes the token and skips audit."""
    store = FakeAuthStore()
    service = AuthService(store)
    _, token = await _signed_up(service)
    before = len(store.events)

    await service.logout(token, None)

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(token, None)
    assert len(store.events) == before
