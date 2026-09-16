"""The collection registry is the single source of storage names (AGENTS.md rule 5)."""

from __future__ import annotations

from voiceai.database.constants import Collections


def test_collection_names_are_unique() -> None:
    """Two collections sharing a name would silently merge two datasets."""
    values = [member.value for member in Collections]

    assert len(values) == len(set(values))


def test_collection_values_are_lower_snake_of_their_member_name() -> None:
    """The naming convention is mechanical, so a new member cannot invent a style."""
    for member in Collections:
        assert member.value == member.name.lower()


def test_members_compare_as_plain_strings() -> None:
    """Drivers take strings; the enum stays usable wherever a collection name is expected."""
    # why: the name arrives typed as `str`, the way a driver would hand it back — comparing two
    # literals here would also trip mypy's strict_equality literal-overlap check.
    stored_name: str = "health_checks"

    assert Collections.HEALTH_CHECKS == stored_name
    assert f"{Collections.USERS.value}" == "users"
