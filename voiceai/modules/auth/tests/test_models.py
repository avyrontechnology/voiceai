"""Auth schema at its new home (spec 0005, C2).

Each test pins the moved models against concrete values: role calibration,
scope/key gates on truth tables (never bare truthiness), email validation,
session/invite/key/audit shapes and defaults. Timestamps ride
``common.datetime_utils.utc_now``; ids ride ``common.ids.new_id``.
"""

import pytest

from voiceai.common.ids import new_id
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import ALL_SCOPES, ROLE_RANK, ROLE_SCOPES, User


def test_role_ladder_calibration() -> None:
    """Rank order and scope tables match the legacy contract exactly."""
    assert ROLE_RANK == {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
    assert ROLE_SCOPES["owner"] == ["*"]
    assert "users:write" not in ROLE_SCOPES["member"]
    assert "users:read" in ROLE_SCOPES["admin"]
    assert "users:write" not in ROLE_SCOPES["admin"]
    assert "calls:write" in ALL_SCOPES


def test_session_principal_gets_role_scopes() -> None:
    """Session callers inherit their role's scopes."""
    principal = Principal(user_id="u-1", email="a@b.test", role="member")

    assert principal.effective_scopes() == ROLE_SCOPES["member"]
    assert principal.has_scope("calls:write") is True
    assert principal.has_scope("users:write") is False
    assert principal.has_role("member") is True
    assert principal.has_role("admin") is False


def test_key_principal_gets_exact_scopes() -> None:
    """Key callers get exactly their key scopes; owners still gate on `*`."""
    full = Principal(user_id="u-1", email=None, auth_type="key", scopes=["*"])
    narrow = Principal(user_id="u-1", email=None, auth_type="key", scopes=["calls:read"])

    assert full.effective_scopes() == ["*"]
    assert full.has_scope("anything") is True
    assert full.has_role("owner") is True
    assert narrow.effective_scopes() == ["calls:read"]
    assert narrow.has_scope("calls:write") is False
    assert narrow.has_role("viewer") is False


def test_unknown_role_degrades_to_no_access() -> None:
    """A garbage role grants nothing instead of raising."""
    principal = Principal(user_id="u-1", email=None, role="nobody")

    assert principal.effective_scopes() == []
    assert principal.has_scope("calls:read") is False
    assert principal.has_role("viewer") is False


def test_user_defaults_and_email_validation() -> None:
    """Member role, default org, enabled; bad emails rejected at the boundary."""
    user = User(user_id="u-1", email="a@b.test", password_hash="h")

    assert (user.role, user.org_id, user.disabled) == ("member", "default", False)
    assert user.created_at is not None
    with pytest.raises(Exception):
        User(user_id="u-2", email="not-an-email", password_hash="h")


def test_session_invite_key_and_audit_shapes() -> None:
    """Moved records keep their fields, kinds and defaults."""
    session = SessionRecord(token_hash="t", user_id="u-1", expires_at="2030-01-01T00:00:00+00:00")
    invite = Invite(
        invite_id=new_id("inv"),
        email="c@d.test",
        token_hash="t",
        expires_at="2030-01-01T00:00:00+00:00",
    )
    key = ApiKey(key_id="k-1", name="ci", prefix="ak_")
    event = AuthEvent(event_id=new_id("evt"), type="login")

    assert session.kind == "session"
    assert session.org_id == "default"
    assert invite.accepted is False
    assert invite.invite_id.startswith("inv_")
    assert key.scopes == [] and key.key_hash is None
    assert event.event_id.startswith("evt_")
    assert event.user_id is None
