"""Unit tests for the Mongo seed script. No database, no driver import."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from seed_mongo_users import ROSTER, build_user_doc, seed_users


def test_roster_is_admin_member_viewer_trio():
    assert [role for _, _, role in ROSTER] == ["admin", "member", "viewer"]
    assert len({email for email, _, _ in ROSTER}) == 3


def test_build_user_doc_validates_through_user_model():
    from voiceai.platform.auth import verify_password
    from voiceai.platform.models import User

    doc = build_user_doc("test-member@example.com", "Test Member", "member", "s3cure-pass")
    assert User(**doc).email == "test-member@example.com"
    assert doc["password_hash"].startswith("pbkdf2_sha256$")
    assert "s3cure-pass" not in str(doc)
    assert verify_password("s3cure-pass", doc["password_hash"]) is True
    assert verify_password("wrong-pass", doc["password_hash"]) is False


def test_build_user_doc_rejects_bad_input():
    with pytest.raises(ValueError):
        build_user_doc("test-member@example.com", "Test Member", "member", "short")
    with pytest.raises(Exception):
        build_user_doc("not-an-email", "Nope", "member", "s3cure-pass")
    with pytest.raises(Exception):
        build_user_doc("test-member@example.com", "Test Member", "superuser", "s3cure-pass")


def test_seed_users_dry_run_touches_no_db(monkeypatch):
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("MONGO_USER", raising=False)
    results = seed_users("obota-ai", "users", "s3cure-pass", dry_run=True)
    assert [(r["email"], r["role"], r["action"]) for r in results] == [
        ("test-admin@example.com", "admin", "validated"),
        ("test-member@example.com", "member", "validated"),
        ("test-viewer@example.com", "viewer", "validated"),
    ]
