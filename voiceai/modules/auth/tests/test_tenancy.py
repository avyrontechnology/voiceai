"""Auth tenancy: org-bound reads, stamped writes, per-org owner safety (spec 0020, M1b).

Tenant ids reuse `org_id` verbatim; the models sync `tenant_id` from `org_id` on
every validation, so these tests pin the service-layer boundary: cross-org rows
read as missing, listings stay inside the caller's org, the last-owner guard
counts per org, and audit rows carry the subject's tenant.
"""

from __future__ import annotations

import pytest

from voiceai.common.errors import InvalidRequestError
from voiceai.modules.auth.errors import AuthNotFoundError
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.conftest import _JWT
from voiceai.modules.auth.tests.test_service import FakeAuthStore, _owner_principal, _signed_up


async def _two_orgs() -> tuple[AuthService, FakeAuthStore, User, User]:
    """One service holding an owner in org `acme` and one in org `globex`.

    The store is global (discovery layer); org separation lives in the service.
    Users are written straight to the store — signup only ever mints the first.
    """
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    acme, _ = await _signed_up(service, email="owner@acme.test")
    acme.org_id = "acme"
    acme.tenant_id = "acme"
    await store.save_user(acme)
    globex = User(
        user_id="usr-globex",
        email="owner@globex.test",
        name="Globex",
        password_hash="x",
        role="owner",
        org_id="globex",
    )
    await store.save_user(globex)
    return service, store, acme, globex


def _principal_for(user: User) -> Principal:
    """Act-as principal for a user (mirrors the session resolver's mapping)."""
    return _owner_principal(user)


async def test_models_sync_tenant_from_org() -> None:
    """Every validation stamps `tenant_id` from `org_id` — writers cannot forget."""
    user = User(user_id="u-1", email="u@x.test", password_hash="x", org_id="acme")

    assert user.tenant_id == "acme"
    assert User.model_validate(user.model_dump()).tenant_id == "acme"


async def test_list_users_stays_inside_the_callers_org() -> None:
    """Admin listing never crosses the org boundary."""
    service, _, acme, _ = await _two_orgs()

    users = await service.list_users(_principal_for(acme))

    assert [u.email for u in users] == ["owner@acme.test"]


async def test_cross_org_user_reads_as_missing() -> None:
    """Role change and delete on another org's user fail as not-found (no oracle)."""
    service, _, acme, globex = await _two_orgs()

    with pytest.raises(AuthNotFoundError):
        await service.set_user_role(_principal_for(acme), globex.user_id, "member")
    with pytest.raises(AuthNotFoundError):
        await service.delete_user(_principal_for(acme), globex.user_id)


async def test_last_owner_guard_counts_per_org() -> None:
    """Removing acme's only *active* owner is blocked even though globex has one.

    A disabled acme owner acts (role gate passes on role alone): without the org
    scope the global owner count would wave the delete through and leave acme
    ownerless; with it, acme's single active owner is protected.
    """
    service, store, acme, _ = await _two_orgs()
    dormant = User(
        user_id="usr-dormant",
        email="dormant@acme.test",
        password_hash="x",
        role="owner",
        org_id="acme",
        disabled=True,
    )
    await store.save_user(dormant)
    globex_owner = User(
        user_id="usr-g2",
        email="g2@globex.test",
        password_hash="x",
        role="owner",
        org_id="globex",
    )
    await store.save_user(globex_owner)

    with pytest.raises(InvalidRequestError, match="last active owner"):
        await service.delete_user(_principal_for(dormant), acme.user_id)

    assert await store.get_user(acme.user_id) is not None


async def test_invite_inherits_the_inviters_org() -> None:
    """Invites carry the inviter's org; acceptance mints the user inside it."""
    service, store, acme, _ = await _two_orgs()

    invite, raw = await service.invite(_principal_for(acme), "new@acme.test", "New", "member")

    assert invite.org_id == "acme"
    assert invite.tenant_id == "acme"
    user, _ = await service.accept_invite(raw, None, "new-pass-1")
    assert (user.org_id, user.tenant_id) == ("acme", "acme")
    assert store.invites[invite.invite_id].accepted is True


async def test_invites_list_and_delete_stay_inside_the_org() -> None:
    """Cross-org invites are invisible to listing and undeletable."""
    service, _, acme, globex = await _two_orgs()
    acme_invite, _ = await service.invite(_principal_for(acme), "a@acme.test", "A", "member")
    globex_invite, _ = await service.invite(_principal_for(globex), "g@globex.test", "G", "member")

    listed = await service.list_invites(_principal_for(acme))

    assert [i.invite_id for i in listed] == [acme_invite.invite_id]
    with pytest.raises(AuthNotFoundError):
        await service.delete_invite(_principal_for(acme), globex_invite.invite_id)


async def test_audit_rows_carry_the_subjects_tenant_and_reads_filter() -> None:
    """Audit writes stamp the tenant; admin reads see only their org's rows."""
    service, store, acme, globex = await _two_orgs()
    await service.audit("login", user_id=acme.user_id, email=acme.email)
    await service.audit("login", user_id=globex.user_id, email=globex.email)
    await service.audit("login_failed", email="nobody@x.test")

    assert [e.tenant_id for e in store.events[-3:]] == ["acme", "globex", None]
    seen = await service.auth_events(_principal_for(acme))

    assert [e.user_id for e in seen] == [acme.user_id]


async def test_me_rejects_a_principal_from_another_org() -> None:
    """A session whose user moved orgs no longer resolves (same gate, no oracle)."""
    service, store, acme, _ = await _two_orgs()
    forged = _owner_principal(acme)
    moved = await store.get_user(acme.user_id)
    assert moved is not None
    moved.org_id = "globex"
    await store.save_user(moved)

    with pytest.raises(AuthNotFoundError):
        await service.me(forged)
