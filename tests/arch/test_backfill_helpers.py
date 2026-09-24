"""Backfill script invariants: offline checks over the migration table (T6)."""

from voiceai.tooling.backfill_upstash_to_atlas import FAMILIES, MIGRATED, _coerce_dates, _model_for


def test_every_migrated_family_is_censused() -> None:
    """Each migrated pattern appears in the census map (no silent family)."""
    censused = {pattern for pattern, _ in FAMILIES}
    for pattern, _collection, _model, _key in MIGRATED:
        assert pattern in censused


def test_models_resolve_to_greenfield_documents() -> None:
    """The migration table names real document models with the natural key."""
    from voiceai.modules.auth.models.apikey import ApiKey
    from voiceai.modules.auth.models.audit import AuthEvent
    from voiceai.modules.auth.models.invite import Invite
    from voiceai.modules.auth.models.user import User

    assert _model_for("User") is User
    assert _model_for("Invite") is Invite
    assert _model_for("ApiKey") is ApiKey
    assert _model_for("AuthEvent") is AuthEvent


def test_sessions_and_throttle_are_never_migrated() -> None:
    """Ephemeral families stay out of the migration table by design."""
    migrated_patterns = {pattern for pattern, _c, _m, _k in MIGRATED}
    assert "platform:v1:sessions:*" not in migrated_patterns
    assert "auth:throttle:*" not in migrated_patterns
    assert "platform:v1:revoked:*" not in migrated_patterns


def test_coerce_dates_parses_iso_and_leaves_garbage() -> None:
    """ISO `*_at` strings become datetimes; unparseable values pass through."""
    from datetime import datetime

    payload = _coerce_dates({"created_at": "2026-09-22T19:00:00+00:00", "name": "x", "n_at": "nope"})

    assert isinstance(payload["created_at"], datetime)
    assert payload["name"] == "x"
    assert payload["n_at"] == "nope"


def test_seed_roster_covers_every_role_with_unmailable_addresses() -> None:
    """The seeder roster has one row per role, all on non-routable example.com."""
    from voiceai.tooling.seed_users import ROSTER

    assert [role for _email, _name, role in ROSTER] == ["owner", "admin", "member", "viewer"]
    assert len({email for email, _name, _role in ROSTER}) == len(ROSTER)
    assert all(email.endswith("@example.com") for email, _name, _role in ROSTER)
