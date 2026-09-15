"""Tests for voiceai.database (US1, task T012).

BaseDocument audit fields and the collection-name registry
(data-model.md Entities 1-2). Model instantiation needs no live Mongo.
"""

from datetime import datetime

import pytest
from beanie import Document

from voiceai.database.base import AuditFields, BaseDocument
from voiceai.database.constants import COLLECTIONS


def test_audit_mixin_binds_to_beanie_document() -> None:
    """BaseDocument is a Beanie Document carrying the audit fields."""

    class _Widget(BaseDocument):
        name: str = ""

        class Settings:
            name = "widgets_test_only"

    assert issubclass(BaseDocument, Document)
    assert issubclass(BaseDocument, AuditFields)
    for audit_field in ("created_at", "updated_at", "created_by", "updated_by", "is_active", "meta"):
        assert audit_field in _Widget.model_fields


def test_audit_defaults() -> None:
    """New records default to active with UTC stamps, no actor, empty meta."""
    record = AuditFields()
    assert record.is_active is True
    assert record.meta == {}
    assert record.created_by is None
    assert record.updated_by is None
    assert record.created_at.tzinfo is not None
    assert record.updated_at.tzinfo is not None


def test_naive_datetimes_rejected() -> None:
    """Naive datetimes are rejected per data-model Entity 1 (tz-aware UTC only)."""
    with pytest.raises(ValueError):
        AuditFields(created_at=datetime(2026, 9, 15, 12, 0, 0))


def test_touch_and_activation_transitions() -> None:
    """touch() refreshes stamp+actor; deactivate/reactivate flip is_active."""
    record = AuditFields(created_by="alice")
    record.deactivate("bob")
    assert record.is_active is False
    assert record.updated_by == "bob"
    record.reactivate("carol")
    assert record.is_active is True
    assert record.updated_by == "carol"
    before = record.updated_at
    record.touch("dave")
    assert record.updated_by == "dave"
    assert record.updated_at >= before


def test_registry_covers_known_domains() -> None:
    """Registry holds every known collection exactly once, lowercase values."""
    for key in ("USERS", "ORGANIZATIONS", "API_KEYS", "SESSIONS", "EXECUTIONS", "BATCHES"):
        assert key in COLLECTIONS, f"missing registry entry: {key}"
    values = list(COLLECTIONS.values())
    assert len(values) == len(set(values)), "duplicate collection names in registry"
    assert all(value == value.lower() for value in values)


def test_every_registry_value_is_a_usable_collection_name() -> None:
    """Every registry value initializes as a Settings.name (usable by models)."""

    for value in COLLECTIONS.values():

        class _Doc(BaseDocument):
            class Settings:
                name = value

        assert _Doc.Settings.name == value
