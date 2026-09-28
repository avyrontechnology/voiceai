"""Slice A lookup units (spec 0047): normalization matrix, assigned/unassigned,
unparseable, and the duplicate-assignment behavior pin — all against fakes.

The fake store implements the `InboundLookupStore` port in-memory, standing in
for the integrator's legacy-store adapter (which the Slice D arch pin proves
never leaks a `voiceai.platform` import into this module).
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from voiceai.modules.voice.session.inbound import (
    InboundLookupStore,
    NumberAssignment,
    normalize_called_number,
    resolve_inbound,
)


class FakeInboundStore:
    """In-memory `InboundLookupStore` double (plain dicts only, no platform types)."""

    def __init__(
        self,
        assignments: list[NumberAssignment],
        configs: dict[str, dict[str, Any]],
    ) -> None:
        self._assignments = assignments
        self._configs = configs
        self.config_lookups: list[str] = []

    async def list_assignments(self) -> list[NumberAssignment]:
        return list(self._assignments)

    async def get_inbound_config(self, agent_id: str) -> dict[str, Any] | None:
        self.config_lookups.append(agent_id)
        return self._configs.get(agent_id)


def _store(
    numbers: list[tuple[str, str | None]],
    configs: dict[str, dict[str, Any]] | None = None,
) -> FakeInboundStore:
    assignments = [NumberAssignment(number=number, assigned_agent_id=agent_id) for number, agent_id in numbers]
    return FakeInboundStore(assignments, configs or {})


def _config(agent_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"agent_id": agent_id, "spam_protection": True, "caller_match_source": "none"}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+14155552671", "+14155552671"),
        ("+1 (415) 555-2671", "+14155552671"),
        ("+1-415-555-2671", "+14155552671"),
        ("+44 20 7946 0958", "+442079460958"),
        ("  +14155552671  ", "+14155552671"),
        ("+\t14155552671", "+14155552671"),
        ("((+14155552671))", "+14155552671"),
    ],
)
def test_normalize_called_number_matrix(raw: str, expected: str) -> None:
    assert normalize_called_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "4155552671",  # missing leading + is unparseable, never patched up
        "+",
        "+-() ",
        "++14155552671",
        "+abc",
        "+1.415.555.2671",  # dots are not in the strip set (spec-literal)
        "+1/415/5552671",
        "+12",  # below the E.164 floor
        "+1234567890123456",  # above the E.164 ceiling
    ],
)
def test_normalize_called_number_unparseable(raw: str) -> None:
    assert normalize_called_number(raw) is None


async def test_resolve_inbound_non_string_never_raises() -> None:
    store = _store([("+14155552671", "agent-1")], {"agent-1": _config("agent-1")})
    assert await resolve_inbound(cast(str, None), store) is None


async def test_resolve_inbound_assigned_returns_agent_and_config() -> None:
    config = _config("agent-1", greeting="Hello, thanks for calling.")
    store = _store([("+14155552671", "agent-1")], {"agent-1": config})
    assert await resolve_inbound("+14155552671", store) == ("agent-1", config)
    assert store.config_lookups == ["agent-1"]


async def test_resolve_inbound_matches_across_formatting_variants() -> None:
    config = _config("agent-1")
    store = _store([("+1 (415) 555-2671", "agent-1")], {"agent-1": config})
    assert await resolve_inbound("+1-415-555-2671", store) == ("agent-1", config)


async def test_resolve_inbound_unknown_number_is_none() -> None:
    store = _store([("+14155552671", "agent-1")], {"agent-1": _config("agent-1")})
    assert await resolve_inbound("+19998887777", store) is None
    assert store.config_lookups == []


async def test_resolve_inbound_unassigned_row_is_none() -> None:
    store = _store([("+14155552671", None)], {"agent-1": _config("agent-1")})
    assert await resolve_inbound("+14155552671", store) is None
    assert store.config_lookups == []


async def test_resolve_inbound_unparseable_caller_is_none() -> None:
    store = _store([("+14155552671", "agent-1")], {"agent-1": _config("agent-1")})
    assert await resolve_inbound("4155552671", store) is None
    assert store.config_lookups == []


async def test_resolve_inbound_garbage_rows_are_skipped() -> None:
    config = _config("agent-1")
    store = _store(
        [("not-a-number", "agent-2"), ("", "agent-3"), ("+14155552671", "agent-1")],
        {"agent-1": config},
    )
    assert await resolve_inbound("+14155552671", store) == ("agent-1", config)


async def test_resolve_inbound_missing_config_row_yields_defaults() -> None:
    store = _store([("+14155552671", "agent-1")])
    assert await resolve_inbound("+14155552671", store) == ("agent-1", {})


async def test_resolve_inbound_same_agent_duplicate_rows_still_resolve() -> None:
    config = _config("agent-1")
    store = _store(
        [("+14155552671", "agent-1"), ("+1 (415) 555-2671", "agent-1")],
        {"agent-1": config},
    )
    assert await resolve_inbound("+14155552671", store) == ("agent-1", config)


async def test_resolve_inbound_cross_agent_duplicate_fails_closed() -> None:
    """Pin the triage behavior: ambiguous ownership rejects like unknown (None).

    The legacy assign path has no 409 (see report), so duplicates are possible;
    first-wins would silently route one tenant's call into another tenant's
    agent — fail-closed is the tenant-safe answer.
    """
    store = _store(
        [("+14155552671", "agent-1"), ("+1-415-555-2671", "agent-2")],
        {"agent-1": _config("agent-1"), "agent-2": _config("agent-2")},
    )
    assert await resolve_inbound("+14155552671", store) is None
    assert store.config_lookups == []


def test_fake_satisfies_port_shape() -> None:
    """The fake must stay structurally compatible with the port (mypy enforces)."""
    store: InboundLookupStore = _store([])
    assert store is not None
