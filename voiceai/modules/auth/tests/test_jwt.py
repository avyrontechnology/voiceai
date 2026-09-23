"""JWT flows: static issue/verify plus service pair lifecycle (T2).

Static primitives are time-deterministic (callers pass `now`); service tests drive
the REAL `AuthService` over the dict-backed fake with test-only RS256 keys from
the module conftest. What is pinned: pair shape, rotation single-use, reuse
quarantine, version kill, denylist single-logout, tampered/expired rejection, and
loud darkness without keys.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from voiceai.common.errors import InvalidRequestError
from voiceai.modules.auth.errors import InvalidCredentialsError
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.static_methods import (
    issue_access_token,
    new_token,
    token_hash,
    verify_access_token,
)
from voiceai.common.datetime_utils import utc_now
from voiceai.modules.auth.tests.conftest import _JWT
from voiceai.modules.auth.tests.test_service import FakeAuthStore, _owner_principal, _signed_up


def _claims() -> dict[str, object]:
    """One valid claim set for the static-primitive tests."""
    return {
        "user_id": "usr_test",
        "org_id": "default",
        "role": "member",
        "token_version": 0,
        "jti": new_token(),
    }


def test_issue_verify_round_trip() -> None:
    """A minted token verifies to the same claims."""
    token = issue_access_token(
        user_id="usr_test",
        org_id="default",
        role="member",
        token_version=3,
        jti="jti-1",
        issuer=_JWT.issuer,
        audience=_JWT.audience,
        access_ttl_s=900,
        private_key=_JWT.private_key,
        now=utc_now(),
    )
    claims = verify_access_token(token, public_key=_JWT.public_key, issuer=_JWT.issuer, audience=_JWT.audience)

    assert claims is not None
    assert (claims["sub"], claims["ver"], claims["jti"]) == ("usr_test", 3, "jti-1")


def test_verify_rejects_tampered_wrong_key_and_expired() -> None:
    """Signature, key, lifetime and shape failures all read `None` (never raise)."""
    token = issue_access_token(
        **_claims(),  # type: ignore[arg-type]  # why: test claim bundle matches the static signature
        issuer=_JWT.issuer,
        audience=_JWT.audience,
        access_ttl_s=900,
        private_key=_JWT.private_key,
        now=utc_now(),
    )
    other = issue_access_token(
        **_claims(),  # type: ignore[arg-type]  # why: test claim bundle matches the static signature
        issuer="someone-else",
        audience=_JWT.audience,
        access_ttl_s=900,
        private_key=_JWT.private_key,
        now=utc_now(),
    )
    stale = issue_access_token(
        **_claims(),  # type: ignore[arg-type]  # why: test claim bundle matches the static signature
        issuer=_JWT.issuer,
        audience=_JWT.audience,
        access_ttl_s=900,
        private_key=_JWT.private_key,
        now=utc_now() - timedelta(seconds=901),
    )

    assert (
        verify_access_token(token[:-4] + "AAAA", public_key=_JWT.public_key, issuer=_JWT.issuer, audience="x") is None
    )
    assert verify_access_token(token, public_key=_JWT.public_key, issuer=_JWT.issuer, audience="nope") is None
    assert verify_access_token(other, public_key=_JWT.public_key, issuer=_JWT.issuer, audience=_JWT.audience) is None
    assert verify_access_token(stale, public_key=_JWT.public_key, issuer=_JWT.issuer, audience=_JWT.audience) is None
    assert (
        verify_access_token("not-a-jwt", public_key=_JWT.public_key, issuer=_JWT.issuer, audience=_JWT.audience) is None
    )


async def test_login_pair_authenticates_over_bearer() -> None:
    """The access token authorizes; the refresh row exists with the user stamp."""
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    user, tokens = await _signed_up(service)

    principal = await service.authenticate(None, f"Bearer {tokens.access_token}")

    assert principal.user_id == user.user_id
    assert principal.token_id is not None
    assert principal.effective_scopes() == ["*"]
    assert len([s for s in store.sessions.values() if s.kind == "refresh"]) == 1


async def test_refresh_rotates_single_use() -> None:
    """Rotation hands a fresh pair and kills the presented refresh row."""
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    user, tokens = await _signed_up(service)

    same, rotated = await service.refresh(tokens.refresh_token, client_ip="10.2.2.2")

    assert same.user_id == user.user_id
    assert rotated.access_token != tokens.access_token
    assert rotated.refresh_token != tokens.refresh_token
    with pytest.raises(InvalidCredentialsError):
        await service.refresh(tokens.refresh_token, client_ip="10.2.2.2")
    assert (await service.authenticate(None, f"Bearer {rotated.access_token}")).user_id == user.user_id


async def test_refresh_reuse_after_version_bump_quarantines() -> None:
    """A live refresh row trailing the user version kills every session (stolen-secret path)."""
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    user, tokens = await _signed_up(service)

    user.token_version += 1
    await store.save_user(user)

    with pytest.raises(InvalidCredentialsError):
        await service.refresh(tokens.refresh_token, client_ip="10.2.2.3")
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(tokens.legacy_token, None)
    assert [e.type for e in store.events].count("refresh_reuse_detected") == 1


async def test_role_change_kills_outstanding_access_tokens() -> None:
    """Version bump (no denylist write) is enough: old JWTs trail the row."""
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    owner, owner_tokens = await _signed_up(service)
    member, member_tokens = await service.accept_invite(
        (await service.invite(_owner_principal(owner), "r@x.test", None, "member"))[1], None, "r-pass-1"
    )

    await service.set_user_role(_owner_principal(owner), member.user_id, "admin")

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, f"Bearer {member_tokens.access_token}")
    assert (await service.authenticate(None, f"Bearer {owner_tokens.access_token}")).user_id == owner.user_id


async def test_logout_denies_only_the_presented_jwt() -> None:
    """Single logout writes the denylist; sibling tokens keep working."""
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    user, tokens = await _signed_up(service)
    _, other = await service.login(user.email, "owner-pass-1", False, client_ip="10.2.2.4")
    principal = await service.authenticate(None, f"Bearer {tokens.access_token}")

    await service.logout(None, None, principal)

    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(None, f"Bearer {tokens.access_token}")
    assert (await service.authenticate(None, f"Bearer {other.access_token}")).user_id == user.user_id


async def test_jwt_dark_service_fails_loudly() -> None:
    """Without keys every pair flow raises instead of minting half a session."""
    service = AuthService(FakeAuthStore())
    user = User(user_id="u1", email="a@x.test", password_hash="x", role="owner")
    await service._store.save_user(user)

    with pytest.raises(InvalidRequestError, match="not configured"):
        await service._new_jwt_pair(user)
