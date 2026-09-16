"""Behaviour of the audit envelope every persisted document inherits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from voiceai.database.base import BaseFields


def test_defaults_are_audit_ready() -> None:
    """A fresh document is active, unidentified, and stamped with aware UTC timestamps."""
    document = BaseFields()

    assert document.id is None
    assert document.created_by is None
    assert document.updated_by is None
    assert document.is_active is True
    assert document.meta == {}
    assert document.created_at.tzinfo is not None
    assert document.created_at.utcoffset() == timedelta(0)
    assert document.updated_at.tzinfo is not None


def test_meta_is_not_shared_between_documents() -> None:
    """The mutable default is per-instance, so one document cannot leak meta into another."""
    first = BaseFields()
    second = BaseFields()

    first.meta["tenant"] = "acme"

    assert second.meta == {}


def test_touch_bumps_updated_at_only() -> None:
    """Touching records the write time without rewriting creation history."""
    past = datetime(2024, 1, 1, tzinfo=timezone.utc)
    document = BaseFields(created_at=past, updated_at=past, created_by="creator")

    document.touch()

    assert document.updated_at > past
    assert document.created_at == past
    assert document.created_by == "creator"


def test_touch_records_the_actor_when_given() -> None:
    """A user-driven write attributes the change to that user."""
    document = BaseFields()

    document.touch("user-1")

    assert document.updated_by == "user-1"


def test_touch_without_actor_keeps_the_last_known_one() -> None:
    """A system write must not erase who last changed the document."""
    document = BaseFields(updated_by="user-1")

    document.touch()

    assert document.updated_by == "user-1"
