"""Voice call service: the engine seam the WS entrypoint delegates to (spec 0004 B4).

`VoiceCallService.run_call` drives one full call exactly as the quickstart WS handler
always did: build the legacy ``AssistantManager`` through the injected factory, iterate
its task outputs, and best-effort record the execution for the platform layer in a
``finally``. Pure delegation — AssistantManager → TaskManager behavior is unchanged —
and the socket lifecycle (accept / auth / disconnect handling / close) stays with the
caller: any exception from the run, including a websocket disconnect, propagates AFTER
the execution record fires, so the handler's own ``except`` arms keep working.

Dependencies arrive through the constructor (AGENTS.md rule 9); the legacy-touching
implementations live in ``adapters/manager.py`` and are bound by this module's
``register()``. This file imports no legacy code and no HTTP types.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Final, Protocol

from voiceai.common.errors import NotFoundError
from voiceai.modules.voice import static_methods
from voiceai.modules.voice.constants import PARTNER_ID_KEY, PROVIDER_TALKO
from voiceai.modules.voice.errors import PlaceCallError, TalkoPartnerExistsError
from voiceai.modules.voice.exceptions import ensure_recipient_dialable, ensure_talko_partner_known
from voiceai.modules.voice.helpers import talko_partner_view
from voiceai.modules.voice.models import (
    PlacedCall,
    TalkoPartnerConfig,
)
from voiceai.modules.voice.ports.outbound import OutboundDialPort
from voiceai.modules.voice.repository import PlaceCallRepository
from voiceai.modules.voice.schemas import VoiceContract
from voiceai.modules.voice.session.prompts import prompt_responses_from_store

ConnectTalkoPartnerRequest = VoiceContract.ConnectTalkoPartnerRequest
CreateTalkoPartnerRequest = VoiceContract.CreateTalkoPartnerRequest
PlaceCallRequest = VoiceContract.PlaceCallRequest
TalkoPartnerPreview = VoiceContract.TalkoPartnerPreview
TalkoPartnerView = VoiceContract.TalkoPartnerView
UpdateTalkoPartnerRequest = VoiceContract.UpdateTalkoPartnerRequest

__all__ = [
    "AssistantManagerFactory",
    "AssistantRunHandle",
    "ExecutionRecorder",
    "VoiceCallService",
]

#: Direction label the WS entrypoint has always recorded for engine executions.
DIRECTION_INBOUND: Final[str] = "inbound"

#: The exact legacy warning shape for a failed/unavailable execution record.
_LOG_EXECUTION_SKIPPED: Final[str] = "Execution logging skipped: %s"


class AssistantRunHandle(Protocol):
    """The slice of ``AssistantManager`` the service drives: the run generator.

    ``run_id`` is deliberately NOT part of the protocol — the legacy handler read it
    with ``getattr(..., None)`` and the service preserves that tolerance.
    """

    def run(self, local: bool = ...) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """Yield ``(task_id, task_output)`` per finished task, in order."""
        ...


class AssistantManagerFactory(Protocol):
    """Builds the engine manager for one call (the adapter provides the legacy one)."""

    def __call__(
        self,
        agent_config: dict[str, Any],
        ws: Any,  # why: the engine treats the websocket opaquely (no HTTP types in this layer)
        assistant_id: Any,  # why: legacy accepts any identifier-ish value
        *,
        is_web_based_call: Any,  # why: raw truthiness flag, forwarded verbatim
        prompt_responses: dict[str, Any] | None = None,
    ) -> AssistantRunHandle:
        """Return a run handle for the given agent config and socket."""
        ...


class ExecutionRecorder(Protocol):
    """Persists one finished engine run (the adapter wraps the platform hook)."""

    async def __call__(
        self,
        store: Any,  # why: optional duck-typed platform store
        *,
        agent_id: str,
        run_id: Any,  # why: getattr-sourced legacy value, may be None
        history: list[dict[str, Any]],
        task_outputs: list[Any],  # why: engine task outputs are free-form legacy dicts
        direction: str,
        output: dict[str, Any] | None,
    ) -> None:
        """Record the run; implementations may raise — the service swallows and logs."""
        ...


class VoiceCallService:
    """Runs one live voice call end to end over an accepted websocket.

    Stateless across calls: every per-call object is local to ``run_call``, so one
    registered instance serves the whole process (the container binds it singleton).
    """

    def __init__(
        self,
        *,
        manager_factory: AssistantManagerFactory,
        execution_recorder: ExecutionRecorder,
        logger: logging.Logger,
        session_store: Any = None,  # why: AgentSessionStorePort; None keeps the legacy prompt fetch
        place_repository: PlaceCallRepository | None = None,
        outbound: OutboundDialPort | None = None,
        talko_service_base_url: str = "",
    ) -> None:
        """Wire the service's collaborators (composition happens in ``register()``).

        Args:
            manager_factory: Builds the engine manager for one call.
            execution_recorder: Persists the finished run, best-effort.
            logger: The module's ``otobaai.voice`` logger.
            session_store: The agents module's prompt-payload port (B13a seam), or
                ``None`` when uncomposed — the legacy prompt fetch then still fires.
            place_repository: Outbound-call storage (spec 0008); `None` keeps
                place-call methods failing closed for compositions that only run calls.
            outbound: Outbound-dial port (spec 0008); `None` likewise fails closed.
            talko_service_base_url: Talko-service base for partner-DID fetch
                (spec 0009; from `Environment`, never the request).
        """
        self._manager_factory = manager_factory
        self._execution_recorder = execution_recorder
        self._logger = logger
        self._session_store = session_store
        self._place_repository = place_repository
        self._outbound = outbound
        self._talko_service_base_url = talko_service_base_url

    async def run_call(
        self,
        *,
        agent_config: dict[str, Any],
        ws: Any,  # why: the engine treats the websocket opaquely (no HTTP types in this layer)
        agent_id: str,
        is_web_based_call: Any = False,  # why: raw truthiness flag, forwarded verbatim
        platform_store: Any = None,  # why: optional duck-typed platform store from app state
    ) -> list[dict[str, Any]]:
        """Run every task of the agent over the socket, then record the execution.

        Behavior-preserving move of the quickstart WS handler's run loop and ``finally``
        block: outputs are collected (and INFO-logged, the legacy line), the execution
        record always fires — also when the run raises — and its own failures are
        swallowed with the legacy warning. Exceptions from the run itself (websocket
        disconnects included) propagate to the caller after the record.

        Args:
            agent_config: The stored agent configuration to run.
            ws: The accepted websocket the engine speaks over.
            agent_id: The agent's id (also stamped on the execution record).
            is_web_based_call: Browser-leg flag for the engine.
            platform_store: The platform layer's store, or None when unmounted.

        Returns:
            Every task output the run yielded, in order.

        Raises:
            Exception: Whatever the engine run raises, re-raised after the record.
        """
        # B13a prompt seam: prefetch the prompt payload through the agents port so
        # load_prompt's EXISTING prompt_responses kwarg carries it and the legacy
        # get_prompt_responses branch never fires. Any store failure (or no store at
        # all) degrades to None — the legacy fetch then runs exactly as before.
        prompt_responses: dict[str, Any] | None = None
        if self._session_store is not None:
            try:
                prompt_responses = await prompt_responses_from_store(self._session_store, agent_id)
            except Exception as prefetch_error:  # why: prefetch must never fail the call
                self._logger.warning("Prompt prefetch skipped: %s", prefetch_error)
                prompt_responses = None
        # The kwarg travels only when the store served: factories written against the
        # B4 contract (without prompt_responses) keep working unchanged.
        factory_kwargs: dict[str, Any] = {}
        if prompt_responses is not None:
            factory_kwargs["prompt_responses"] = prompt_responses
        assistant_manager = self._manager_factory(
            agent_config, ws, agent_id, is_web_based_call=is_web_based_call, **factory_kwargs
        )
        task_outputs: list[dict[str, Any]] = []
        try:
            async for _task_id, task_output in assistant_manager.run(local=True):
                # TODO(spec-0004): preserved quirk — the legacy handler INFO-logs the full
                # task output (transcript included); PII debt owned by the platform strangler.
                self._logger.info(task_output)
                task_outputs.append(task_output)
        finally:
            await self._record_execution(agent_id, assistant_manager, task_outputs, platform_store)
        return task_outputs

    async def _record_execution(
        self,
        agent_id: str,
        assistant_manager: AssistantRunHandle,
        task_outputs: list[dict[str, Any]],
        platform_store: Any,  # why: optional duck-typed platform store
    ) -> None:
        """Best-effort execution log for the platform layer; never breaks the call path.

        Verbatim semantics of the legacy handler's ``finally`` body: the last
        conversation payload (the newest output carrying ``messages``) supplies the
        transcript and true timings, and ANY failure — including an unimportable
        platform layer inside the recorder — lands as the one legacy warning.
        """
        try:
            # Last conversation payload carries the transcript, true call timings,
            # latency breakdown and hangup detail — without it every browser-leg
            # row lands with an empty transcript and ~0s duration.
            last_output = next(
                (output for output in reversed(task_outputs) if isinstance(output, dict) and output.get("messages")),
                None,
            )
            await self._execution_recorder(
                platform_store,
                agent_id=agent_id,
                run_id=getattr(assistant_manager, "run_id", None),
                history=[],
                task_outputs=task_outputs,
                direction=DIRECTION_INBOUND,
                output=last_output,
            )
        except Exception as hook_error:  # why: legacy contract — recording must never fail the call
            self._logger.warning(_LOG_EXECUTION_SKIPPED, hook_error)

    async def place_call(self, *, payload: PlaceCallRequest) -> PlacedCall:
        """Place one outbound call: validate, resolve credentials, dial, persist (spec 0008).

        Credential precedence per field: explicit per-request value > partner DB
        record > trunk default. Validation failures raise before any dial; trunk
        refusals come back as `failed` rows, never raises.

        Args:
            payload: The validated place-call request.

        Returns:
            The persisted placed-call view (`IN_PROGRESS` for accepted trunk
            dials — outcome lives in Tata's CDR; `completed` for inline
            simulated runs; `queued` for background simulated runs).

        Raises:
            PlaceCallError: Undialable destination or unwired outbound port.
            UnknownTalkoPartnerError: `partner_id` names no stored record.
        """
        repo = self._require_place_store()
        if self._outbound is None:
            raise PlaceCallError("Outbound calling is not wired for this service.")
        digits = ensure_recipient_dialable(payload.to_number)
        api_key = payload.talko_api_key
        caller_did = static_methods.normalize_did_digits(payload.from_number)
        api_base: str | None = None
        record_dids: list[str] = []
        if payload.partner_id:
            record = await repo.get_partner(payload.partner_id)
            known = ensure_talko_partner_known(payload.partner_id, record)
            api_key = api_key or known.talko_api_key or None
            caller_did = caller_did or static_methods.normalize_did_digits(known.default_did)
            api_base = known.talko_api_base_url
            record_dids = list(known.dids)
        if record_dids and caller_did and caller_did not in record_dids:
            raise PlaceCallError(
                f"Caller DID {caller_did!r} is not one of partner {payload.partner_id!r}'s DIDs.",
                details={PARTNER_ID_KEY: payload.partner_id},
            )
        if payload.provider == PROVIDER_TALKO:
            outcome = await self._outbound.dial_trunk_call(
                agent_id=payload.agent_id,
                to_number=digits,
                from_number=caller_did,
                talko_api_key=api_key,
                partner_id=payload.partner_id,
                talko_api_base_url=api_base,
                variables=dict(payload.variables),
            )
        elif payload.delay_scale == 0:
            outcome = await self._outbound.run_simulated_call_inline(
                agent_id=payload.agent_id,
                to_number=digits,
                from_number=caller_did,
                variables=dict(payload.variables),
            )
        else:
            outcome = await self._outbound.start_simulated_call_background(
                agent_id=payload.agent_id,
                to_number=digits,
                from_number=caller_did,
                variables=dict(payload.variables),
                delay_scale=payload.delay_scale,
            )
        placed = PlacedCall(
            execution_id=outcome.execution_id,
            agent_id=payload.agent_id,
            to_number=payload.to_number,
            from_number=outcome.from_number,
            status=outcome.status,
            provider=payload.provider,
            variables=dict(payload.variables),
        )
        return await repo.save_execution(placed)

    async def create_partner(self, *, payload: CreateTalkoPartnerRequest) -> TalkoPartnerView:
        """Store a partner credential record, rejecting duplicate ids (spec 0008).

        Args:
            payload: The validated creation body (DID normalized on write).

        Returns:
            The secret-free view of the stored record.

        Raises:
            TalkoPartnerExistsError: When `partner_id` is taken (409 at the boundary).
            PlaceCallError: When outbound storage is not wired.
        """
        repo = self._require_place_store()
        if await repo.get_partner(payload.partner_id) is not None:
            raise TalkoPartnerExistsError(
                f"Talko partner {payload.partner_id!r} already exists.",
                details={PARTNER_ID_KEY: payload.partner_id},
            )
        record = TalkoPartnerConfig(
            partner_id=payload.partner_id,
            display_name=payload.display_name,
            talko_api_base_url=payload.talko_api_base_url,
            talko_api_key=payload.talko_api_key,
            default_did=static_methods.normalize_did_digits(payload.default_did),
            dids=static_methods.normalize_did_list(payload.dids),
            vendor_config_id=payload.vendor_config_id,
        )
        stored = await repo.save_partner(record)
        return talko_partner_view(stored)

    async def get_partner(self, *, partner_id: str) -> TalkoPartnerView:
        """Read one partner view by natural key (spec 0008).

        Args:
            partner_id: The partner account id.

        Returns:
            The secret-free view.

        Raises:
            NotFoundError: When no record exists for `partner_id`.
        """
        repo = self._require_place_store()
        record = await repo.get_partner(partner_id)
        if record is None:
            raise NotFoundError(f"Talko partner {partner_id!r} not found.", details={PARTNER_ID_KEY: partner_id})
        return talko_partner_view(record)

    async def list_partners(self) -> list[TalkoPartnerView]:
        """List every partner view (spec 0008).

        Returns:
            Secret-free views in insertion order.
        """
        repo = self._require_place_store()
        records = await repo.list_partners()
        return [talko_partner_view(record) for record in records]

    async def update_partner(self, *, partner_id: str, payload: UpdateTalkoPartnerRequest) -> TalkoPartnerView:
        """Patch a partner record; empty key keeps the stored secret (spec 0008).

        Args:
            partner_id: The natural key.
            payload: The validated patch body.

        Returns:
            The secret-free view.

        Raises:
            NotFoundError: When no record exists for `partner_id`.
        """
        repo = self._require_place_store()
        record = await repo.get_partner(partner_id)
        if record is None:
            raise NotFoundError(f"Talko partner {partner_id!r} not found.", details={PARTNER_ID_KEY: partner_id})
        if payload.display_name is not None:
            record.display_name = payload.display_name
        if payload.talko_api_base_url is not None:
            record.talko_api_base_url = payload.talko_api_base_url
        if payload.talko_api_key:
            record.talko_api_key = payload.talko_api_key
        if payload.default_did is not None:
            record.default_did = static_methods.normalize_did_digits(payload.default_did)
        if payload.dids is not None:
            record.dids = static_methods.normalize_did_list(payload.dids)
            if record.default_did and record.default_did not in record.dids:
                record.default_did = record.dids[0] if record.dids else None
        if payload.vendor_config_id is not None:
            record.vendor_config_id = payload.vendor_config_id
        record.touch()
        stored = await repo.save_partner(record)
        return talko_partner_view(stored)

    async def delete_partner(self, *, partner_id: str) -> bool:
        """Soft-delete a partner record (spec 0008).

        Args:
            partner_id: The natural key.

        Returns:
            `True` when a record was marked deleted.

        Raises:
            NotFoundError: When no record exists for `partner_id`.
        """
        repo = self._require_place_store()
        deleted = await repo.delete_partner(partner_id)
        if not deleted:
            raise NotFoundError(f"Talko partner {partner_id!r} not found.", details={PARTNER_ID_KEY: partner_id})
        return True

    def _require_place_store(self) -> PlaceCallRepository:
        """Return outbound storage, or raise when unwired (keeps unit tests DI-pure)."""
        if self._place_repository is None:
            raise PlaceCallError("Outbound calling is not wired for this service.")
        return self._place_repository

    async def preview_partner(self, *, talko_api_key: str) -> TalkoPartnerPreview:
        """Validate a partner key and preview its DIDs without persisting (spec 0009).

        Args:
            talko_api_key: The partner secret (used once, never stored or returned).

        Returns:
            Partner id (when derivable) and normalized DIDs, Mapped first.

        Raises:
            PlaceCallError: Unwired outbound port or misconfigured service base.
        """
        from voiceai.modules.voice.errors import PlaceCallError

        if self._outbound is None:
            raise PlaceCallError("Outbound calling is not wired for this service.")
        base = (self._talko_service_base_url or "").rstrip("/")
        if not base:
            raise PlaceCallError("Talko service base URL is not configured.")
        preview = await self._outbound.fetch_partner_dids(talko_api_key=talko_api_key, talko_api_base_url=base)
        return TalkoPartnerPreview(
            partner_id=preview.get("partner_id"),
            dids=list(preview.get("dids") or []),
        )

    async def connect_partner(
        self,
        *,
        payload: ConnectTalkoPartnerRequest,
    ) -> TalkoPartnerView:
        """Fetch-and-store a partner in one step for the connect UI (spec 0009).

        The first fetched DID becomes the default when the record has none;
        an explicit `partner_id` wins when the fetch cannot derive one.

        Args:
            payload: Key, optional nickname override and partner override.

        Returns:
            The secret-free view of the upserted record.
        """
        preview = await self.preview_partner(talko_api_key=payload.talko_api_key)
        partner_id = payload.partner_id or preview.partner_id
        if not partner_id:
            from voiceai.modules.voice.errors import PlaceCallError

            raise PlaceCallError("Talko returned no DIDs to derive a partner from; pass partner_id explicitly.")
        self._require_place_store()
        existing = await self._place_repository.get_partner(partner_id)  # type: ignore[union-attr]
        if existing is None:
            record = TalkoPartnerConfig(
                partner_id=partner_id,
                display_name=payload.display_name or f"Partner {partner_id}",
                talko_api_key=payload.talko_api_key,
                default_did=preview.dids[0] if preview.dids else None,
                dids=list(preview.dids),
            )
        else:
            record = existing
            if payload.display_name:
                record.display_name = payload.display_name
            record.talko_api_key = payload.talko_api_key
            merged = list(record.dids) + [d for d in preview.dids if d not in record.dids]
            record.dids = merged
            if not record.default_did and merged:
                record.default_did = merged[0]
        stored = await self._place_repository.save_partner(record)  # type: ignore[union-attr]
        return talko_partner_view(stored)

    async def refresh_partner_dids(self, *, partner_id: str) -> TalkoPartnerView:
        """Re-fetch a stored partner's DIDs with its own key, merging additively (spec 0009).

        The stored default is never dropped by a refresh; an empty fetch leaves
        the record untouched.

        Args:
            partner_id: The natural key.

        Returns:
            The secret-free view.

        Raises:
            NotFoundError: When no record exists for `partner_id`.
        """
        self._require_place_store()
        record = await self._place_repository.get_partner(partner_id)  # type: ignore[union-attr]
        if record is None:
            raise NotFoundError(f"Talko partner {partner_id!r} not found.", details={PARTNER_ID_KEY: partner_id})
        preview = await self.preview_partner(talko_api_key=record.talko_api_key)
        if preview.dids:
            record.dids = list(record.dids) + [d for d in preview.dids if d not in record.dids]
            if not record.default_did:
                record.default_did = record.dids[0]
            record.touch()
            record = await self._place_repository.save_partner(record)  # type: ignore[union-attr]
        return talko_partner_view(record)
