"""Carrier-agnostic inbound number-to-agent lookup (spec 0047, Slice A).

A carrier webhook (Twilio first, Plivo/Talko later) arrives with a `called`
number; this module maps it to the assigned agent plus that agent's persisted
inbound config. Unknown, unassigned, and unparseable numbers all resolve to
`None` so the caller rejects identically (spec 0047, Decision 1 — no
cross-tenant oracle via dialing).

The persistence seam is the injected `InboundLookupStore` port: narrow
(list assignments / get inbound config) and free of any
`voiceai.platform` import. The integrator wires the legacy `MemoryStore`
behind it (mapping legacy `PhoneNumber` rows to `NumberAssignment` and
`InboundConfig` to a plain dict). Tenant binding is downstream's job: the
returned agent's tenant owns the call; this lookup exposes single-row-or-None
only, never an enumeration surface.

Duplicate-assignment triage (spec 0047, data model): the legacy assign path
(`platform/router.py::assign_number`) performs NO uniqueness check — no 409,
last-wins overwrite — so two rows CAN share one normalized number. A
cross-agent duplicate is ambiguous about which tenant owns the call, so
`resolve_inbound` fails closed (`None`, identical to unknown) and logs loudly
at WARNING for operator triage instead of silently picking first-wins.

Constants live in this file (not `voice/constants.py`): that file is owned by
a parallel Slice A–E peer this iteration, so importing it would couple this
slice to a concurrently-edited file. The values are E.164 facts, not tunables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Protocol

from voiceai.common.logger import get_logger

__all__ = [
    "InboundLookupStore",
    "MAX_E164_DIGITS",
    "MIN_E164_DIGITS",
    "NumberAssignment",
    "normalize_called_number",
    "resolve_inbound",
]

logger = get_logger("voice.inbound")

#: Characters the E.164 normalization strips: whitespace, dashes, parentheses
#: (spec 0047 Slice A contract — dots, slashes, and other separators stay and
#: therefore fail the digit check, resolving to `None`).
STRIPPABLE_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[\s()\-]+")

#: E.164 digit-count bounds (ITU-T E.164: at most 15 digits; 7 is the practical
#: floor for a routable country-code + national number).
MIN_E164_DIGITS: Final[int] = 7
MAX_E164_DIGITS: Final[int] = 15


@dataclass(frozen=True)
class NumberAssignment:
    """One persisted number row, as the integrator maps it from the legacy store.

    The legacy `PhoneNumber` row carries `number` (free-form at create time —
    no format validation on write, so this lookup normalizes before comparing)
    and `assigned_agent_id` (`None` while unassigned). Rows whose `number`
    does not E.164-normalize are skipped, never matched.
    """

    number: str
    assigned_agent_id: str | None


class InboundLookupStore(Protocol):
    """Injected read seam over the legacy platform phone-number surface.

    The integrator implements this against `MemoryStore`/`RedisStore`
    (`list_numbers` → `NumberAssignment`, `get_inbound` → `model_dump`); this
    module never imports `voiceai.platform` (spec 0047 Slice A contract).
    """

    async def list_assignments(self) -> list[NumberAssignment]:
        """Return every persisted number row (assigned and unassigned)."""
        ...

    # why: the persisted row is an open JSON-ish mapping, not a fixed schema.
    async def get_inbound_config(self, agent_id: str) -> dict[str, Any] | None:
        """Return the agent's persisted inbound config as a plain dict.

        `None` when the agent has no inbound row — the caller then routes on
        config defaults (all `InboundConfig` fields have them).
        """
        ...


def normalize_called_number(called: str) -> str | None:
    """E.164-normalize a called number; unparseable input yields `None`.

    Strips spaces/dashes/parens, requires a leading `+` followed only by
    digits within E.164 length bounds. Never raises — wrong-typed, empty, and
    malformed inputs all return `None` so the webhook path rejects identically.

    Args:
        called: The raw called number (carrier `To` field or stored row value).

    Returns:
        The normalized `+<digits>` number, or `None` when unparseable.
    """
    if not isinstance(called, str):
        return None
    stripped = STRIPPABLE_CHARS_RE.sub("", called)
    if not stripped.startswith("+"):
        return None
    digits = stripped[1:]
    if not digits.isdigit() or not MIN_E164_DIGITS <= len(digits) <= MAX_E164_DIGITS:
        return None
    return stripped


async def resolve_inbound(
    called: str,
    store: InboundLookupStore,
) -> tuple[str, dict[str, Any]] | None:  # why: config is an open JSON-ish mapping passed through untouched
    """Map a carrier `called` number to `(agent_id, inbound-config-dict)`.

    Compares the normalized `called` against every assignment row's normalized
    `number` (stored rows are free-form, so both sides normalize). Unassigned
    rows and rows that do not normalize never match. Returns `None` for
    unparseable input, unknown/unassigned numbers, and cross-agent duplicates
    (fail-closed + WARNING log for triage — never silent first-wins). A missing
    inbound-config row resolves to `{}` (config defaults apply downstream).

    Args:
        called: The raw called number from the carrier webhook `To` field.
        store: The injected lookup seam (legacy store wired behind it).

    Returns:
        `(agent_id, config-dict)` for the uniquely assigned agent, else `None`.
    """
    normalized = normalize_called_number(called)
    if normalized is None:
        return None
    assignments = await store.list_assignments()
    agent_ids = {
        row.assigned_agent_id
        for row in assignments
        if row.assigned_agent_id and normalize_called_number(row.number) == normalized
    }
    if not agent_ids:
        return None
    if len(agent_ids) > 1:
        logger.warning("inbound duplicate assignment: %s matches %d agents; rejecting", normalized, len(agent_ids))
        return None
    agent_id = next(iter(agent_ids))
    config = await store.get_inbound_config(agent_id)
    return agent_id, dict(config) if config is not None else {}
