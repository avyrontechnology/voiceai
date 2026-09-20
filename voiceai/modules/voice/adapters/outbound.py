"""Outbound-dial bridge to the legacy call runners (spec 0008).

The ONLY new-architecture files sanctioned to import legacy packages
(AGENTS.md §3.1 bridge 1): `voiceai.platform.talko_dialer` (real trunk dials)
and `voiceai.platform.simulation` (offline simulated calls). Everything the
service needs crosses this seam as plain data (`DialOutcome`); the legacy
runners persist into a private throwaway store owned here, never into a
shared backend — new-arch persistence lives in `voiceai.modules.voice`
`repository` alone, so no row is ever written twice.

Tagged for retirement: delete this module when the trunk protocol and the
simulated runner migrate out of `voiceai.platform` (strangler endgame); the
service contract (`DialOutcome`) survives unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from voiceai.modules.voice.ports.outbound import DialOutcome, PartnerPreview
from voiceai.platform.models import new_id as _legacy_new_id
from voiceai.platform.simulation import (
    progress_simulated_call as _legacy_progress_simulated_call,
)
from voiceai.platform.simulation import (
    run_simulated_call as _legacy_run_simulated_call,
)
from voiceai.platform.store import MemoryStore as _LegacyMemoryStore
from voiceai.platform.talko_dialer import dial_via_talko as _legacy_dial_via_talko

__all__ = [
    "OutboundDialBridge",
    "dial_trunk_call",
    "fetch_partner_dids",
    "run_simulated_call_inline",
    "start_simulated_call_background",
]


#: Retained background simulated-call tasks (AGENTS.md §5: every `create_task`
#: result is retained; the legacy runner this mirrors never cancelled them, so
#: neither does this bridge — entries drop themselves on completion).
_background_tasks: set[asyncio.Task[Any]] = set()


async def dial_trunk_call(
    *,
    agent_id: str,
    to_number: str,
    from_number: str | None = None,
    talko_api_key: str | None = None,
    partner_id: str | None = None,
    talko_api_base_url: str | None = None,
    variables: dict[str, Any] | None = None,
) -> DialOutcome:
    """Place one real call through the Talko trunk via the legacy runner.

    All credentials arrive resolved (service already applied explicit >
    record > default precedence); this seam only forwards them. Never raises
    for trunk errors — refusals come back as `failed` outcomes, mirroring the
    legacy runner it wraps.

    Args:
        agent_id: Handling agent definition.
        to_number: Destination digits (validated upstream).
        from_number: Caller DID override, if any.
        talko_api_key: Partner key override, if any.
        partner_id: Partner account, forwarded for trunk correlation.
        talko_api_base_url: Service-base override, if any.
        variables: Per-contact data carried onto the execution.

    Returns:
        The dial outcome.
    """
    # NOTE(spec-0008): the legacy runner on this branch takes explicit
    # key/DID only — partner_id rides the trunk body via a future endgame
    # step, and per-partner base URLs activate there too. partner_id and
    # talko_api_base_url are accepted (contract parity) but not forwarded.
    legacy_kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "to_number": to_number,
        "variables": dict(variables or {}),
    }
    if from_number:
        legacy_kwargs["from_number"] = from_number
    if talko_api_key:
        legacy_kwargs["talko_api_key"] = talko_api_key
    execution = await _legacy_dial_via_talko(_LegacyMemoryStore(), **legacy_kwargs)
    return DialOutcome(
        execution_id=execution.execution_id,
        status=execution.status.value,
        summary=execution.summary,
        from_number=execution.from_number,
    )


async def run_simulated_call_inline(
    *,
    agent_id: str,
    to_number: str,
    from_number: str | None = None,
    variables: dict[str, Any] | None = None,
) -> DialOutcome:
    """Run one simulated call to completion inline (deterministic, offline).

    Args:
        agent_id: Handling agent definition.
        to_number: Destination digits (validated upstream).
        from_number: Caller DID override, if any.
        variables: Per-contact data carried onto the execution.

    Returns:
        The completed-call outcome.
    """
    execution = await _legacy_run_simulated_call(
        _LegacyMemoryStore(),
        agent_id=agent_id,
        to_number=to_number,
        from_number=from_number,
        variables=dict(variables or {}),
        delay_scale=0,
    )
    return DialOutcome(
        execution_id=execution.execution_id,
        status=execution.status.value,
        summary=execution.summary,
        from_number=execution.from_number,
    )


async def start_simulated_call_background(
    *,
    agent_id: str,
    to_number: str,
    from_number: str | None = None,
    variables: dict[str, Any] | None = None,
    delay_scale: float = 0.5,
) -> DialOutcome:
    """Queue one simulated call and progress it in the background.

    Mirrors the legacy `/calls/simulate` shape: the caller gets the `queued`
    outcome immediately while progression runs retained (see module docstring).

    Args:
        agent_id: Handling agent definition.
        to_number: Destination digits (validated upstream).
        from_number: Caller DID override, if any.
        variables: Per-contact data carried onto the execution.
        delay_scale: Pacing scale for the background progression.

    Returns:
        The queued-call outcome (the progression updates the legacy row, and
        the service persists its own copy with the same id).
    """
    from voiceai.platform.models import Execution as _LegacyExecution

    execution = _LegacyExecution(
        execution_id=_legacy_new_id("exec"),
        agent_id=agent_id,
        to_number=to_number,
        from_number=from_number,
        variables=dict(variables or {}),
    )
    store = _LegacyMemoryStore()
    await store.save_execution(execution)
    task = asyncio.create_task(_legacy_progress_simulated_call(store, execution.execution_id, delay_scale))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return DialOutcome(
        execution_id=execution.execution_id,
        status=execution.status.value,
        summary=execution.summary,
        from_number=execution.from_number,
    )


class OutboundDialBridge:
    """`OutboundDialPort` over this module's bridge functions (spec 0008).

    Stateless explicit binding so the container injects a typed object instead
    of a module; every method delegates verbatim (documented once, above).
    """

    async def dial_trunk_call(
        self,
        *,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        talko_api_key: str | None = None,
        partner_id: str | None = None,
        talko_api_base_url: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> DialOutcome:
        """Place one real call through the Talko trunk; refusals are outcomes."""
        return await dial_trunk_call(
            agent_id=agent_id,
            to_number=to_number,
            from_number=from_number,
            talko_api_key=talko_api_key,
            partner_id=partner_id,
            talko_api_base_url=talko_api_base_url,
            variables=variables,
        )

    async def run_simulated_call_inline(
        self,
        *,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> DialOutcome:
        """Run one simulated call to completion inline."""
        return await run_simulated_call_inline(
            agent_id=agent_id,
            to_number=to_number,
            from_number=from_number,
            variables=variables,
        )

    async def fetch_partner_dids(
        self,
        *,
        talko_api_key: str,
        talko_api_base_url: str,
    ) -> PartnerPreview:
        """Validate a partner key and fetch its DIDs (spec 0009)."""
        return await fetch_partner_dids(
            talko_api_key=talko_api_key,
            talko_api_base_url=talko_api_base_url,
        )

    async def start_simulated_call_background(
        self,
        *,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        variables: dict[str, Any] | None = None,
        delay_scale: float = 0.5,
    ) -> DialOutcome:
        """Queue one simulated call; progression continues in the background."""
        return await start_simulated_call_background(
            agent_id=agent_id,
            to_number=to_number,
            from_number=from_number,
            variables=variables,
            delay_scale=delay_scale,
        )


async def fetch_partner_dids(
    *,
    talko_api_key: str,
    talko_api_base_url: str,
) -> PartnerPreview:
    """Validate a partner key and fetch its DIDs from talko-service (spec 0009).

    Side-effect free: a single bounded GET against the permission-checked
    `list-dids` endpoint, which returns only the key's own partner scope.
    Nothing is persisted here; the service decides what to store.

    Args:
        talko_api_key: The partner secret (used once as the API-KEY header;
            never logged — failures carry status codes, never the key).
        talko_api_base_url: Service base URL (from `Environment`, never from
            the request — callers must not steer outbound hosts).

    Returns:
        Partner id (derived from the first DID) and digits-normalized DIDs,
        Mapped status first.

    Raises:
        PlaceCallError: The key is rejected (upstream 401).
        DependencyUnavailableError: Transport failure or unexpected upstream
            status (retryable).
    """
    import httpx

    from voiceai.common.errors import DependencyUnavailableError
    from voiceai.modules.voice import static_methods
    from voiceai.modules.voice.constants import TALKO_DIDS_PAGE_SIZE, TALKO_DIDS_PATH, TALKO_FETCH_TIMEOUT_S
    from voiceai.modules.voice.errors import PlaceCallError

    url = f"{talko_api_base_url.rstrip('/')}{TALKO_DIDS_PATH}"
    try:
        async with httpx.AsyncClient(timeout=TALKO_FETCH_TIMEOUT_S) as client:
            response = await client.get(
                url,
                params={"limit": TALKO_DIDS_PAGE_SIZE},
                headers={"API-KEY": talko_api_key},
            )
    except httpx.HTTPError as exc:
        raise DependencyUnavailableError(
            "Talko service unreachable during partner fetch.",
            details={"endpoint": TALKO_DIDS_PATH},
            cause=exc,
        ) from exc
    if response.status_code == 401:
        raise PlaceCallError("Invalid Talko partner key.")
    if response.status_code >= 400:
        raise DependencyUnavailableError(
            f"Talko service refused partner fetch: HTTP {response.status_code}.",
            details={"endpoint": TALKO_DIDS_PATH, "upstream_status": response.status_code},
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise DependencyUnavailableError(
            "Talko service returned a non-JSON partner fetch response.",
            details={"endpoint": TALKO_DIDS_PATH},
            cause=exc,
        ) from exc
    items = (payload.get("data") or {}).get("dids") or []
    normalized: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        digits = static_methods.normalize_did_digits(item.get("did_number"))
        if digits:
            normalized.append((digits, str(item.get("status") or "")))
    normalized.sort(key=lambda pair: (0 if pair[1].lower() == "mapped" else 1, pair[0]))
    dids = [digits for digits, _ in normalized]
    partner_id: str | None = None
    for item in items:
        candidate = item.get("partner_id") if isinstance(item, dict) else None
        if candidate is not None:
            partner_id = str(candidate)
            break
    return PartnerPreview(partner_id=partner_id, dids=dids)
