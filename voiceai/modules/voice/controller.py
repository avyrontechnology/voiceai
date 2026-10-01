"""WS surface of the voice module: wiring only, no logic (AGENTS.md rule 1f; spec 0004, B14).

The realtime call route lives here, mounted by the app factory under its API prefix —
but DARK until the cutover flag (``Environment.voice_ws_enabled``) flips: with the
flag off the handler closes immediately, and quickstart stays the deployed entry
through the whole strangler. The enabled path resolves the agent definition through
the agents port and runs the call through ``VoiceCallService``; every failure mode
answers a close code, never an error body (error opacity, AGENTS.md §4).

Spec 0008 adds the outbound place-call and partner-credential routes. Authentication
resolves through the container ``AuthService`` with per-route scope gates.
"""

import base64
import hashlib
import hmac
import html
from typing import Annotated, Any
from urllib.parse import parse_qsl

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request, WebSocket
from starlette.responses import JSONResponse, Response

from voiceai.common.constants import CONTAINER_STATE_ATTR
from voiceai.common.errors import AppError, ConfigurationError
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.common.responses import error_response, success_response
from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import VoiceAIContainer
from voiceai.core.environment import Environment
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.voice.constants import (
    CHAT_WS_PATH,
    INBOUND_TWILIO_PATH,
    MODULE_NAME,
    PARTNER_ITEM_PATH,
    PARTNER_REFRESH_PATH,
    PARTNERS_CONNECT_PATH,
    PARTNERS_PATH,
    PARTNERS_PREVIEW_PATH,
    PLACE_CALL_PATH,
    TWILIO_SIGNATURE_HEADER,
    TWIML_DEFAULT_GREETING,
    TWIML_MEDIA_TYPE,
    TWIML_REJECT,
    TWIML_SAY_TEMPLATE,
    WS_CLOSE_DARK,
    WS_CLOSE_DENIED,
    WS_CLOSE_UNKNOWN_AGENT,
    WS_LEG_BROWSER_VALUE,
    WS_LEG_PARAM,
    WS_TICKET_PARAM,
)
from voiceai.modules.voice.models import PlacedCall
from voiceai.modules.voice.schemas import VoiceContract
from voiceai.modules.voice.service import VoiceCallService

PlaceCallRequest = VoiceContract.PlaceCallRequest
ConnectTalkoPartnerRequest = VoiceContract.ConnectTalkoPartnerRequest
CreateTalkoPartnerRequest = VoiceContract.CreateTalkoPartnerRequest
UpdateTalkoPartnerRequest = VoiceContract.UpdateTalkoPartnerRequest
TalkoPartnerListResponse = VoiceContract.TalkoPartnerListResponse
TalkoPartnerPreview = VoiceContract.TalkoPartnerPreview
TalkoPartnerView = VoiceContract.TalkoPartnerView

# NOTE: handlers return `JSONResponse` envelopes, which FastAPI serves verbatim —
# `response_model` therefore documents (and freezes, via schema tests) the `data`
# shape in OpenAPI rather than serialising at runtime (the T1 auth/health pattern).

__all__ = ["router"]

logger = get_logger("voice")

router = APIRouter(tags=[MODULE_NAME])


ServiceDep = Annotated[VoiceCallService, Depends(Provide[VoiceAIContainer.voice_call_service])]
EnvironmentDep = Annotated[Environment, Depends(Provide[VoiceAIContainer.environment])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_scope(request: Request, auth: AuthService, scope: str) -> Principal:
    """Gate the caller on a scope, preferring the middleware-stashed principal (one store trip)."""
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get("authorization", "")
    )
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")
    return principal


@router.websocket(CHAT_WS_PATH)
@inject
async def voice_chat(
    websocket: WebSocket,
    agent_id: str,
    environment: EnvironmentDep,
    auth: AuthServiceDep,
) -> None:
    """Run one realtime call over the accepted socket (dark until cutover).

    The channel owns its gate (spec 0021, M2): HTTP middleware never runs on
    websockets, so the handler redeems the single-use ``?ticket=`` itself,
    binds the ticket holder's tenant, and only then resolves the
    request-scoped services from the app container (constructing them earlier
    would read an unbound tenant). Every denial answers a close code, never a
    body; denials log identifiers only, never the token.
    """
    await websocket.accept()
    if not environment.voice_ws_enabled:
        await websocket.close(code=WS_CLOSE_DARK)
        return
    ticket = websocket.query_params.get(WS_TICKET_PARAM)
    principal = await auth.redeem_ticket(ticket)
    if principal is None or not principal.has_scope("calls:write"):
        logger.warning(
            "voice ws denied for agent %s (%s)",
            agent_id,
            "missing ticket" if not ticket else "rejected ticket",
        )
        await auth.audit("ws_denied", detail=agent_id)
        await websocket.close(code=WS_CLOSE_DENIED)
        return
    await auth.audit(
        "ws_connect",
        user_id=principal.user_id,
        email=principal.email,
        detail=agent_id,
    )
    context = TenantContext(
        tenant_id=principal.tenant_id,
        request_id=new_id("ws"),
        principal_id=principal.user_id,
        scopes=frozenset(principal.effective_scopes()),
    )
    with bind_tenant(context):
        container = getattr(websocket.app.state, CONTAINER_STATE_ATTR, None)
        if container is None:
            raise ConfigurationError("tenant resolution is not wired")
        definitions = container.agent_definitions()
        service = container.voice_call_service()
        agent_config = await definitions.get_agent(agent_id) if definitions is not None else None
        if not agent_config:
            logger.warning("voice ws closed for agent %s (unknown or foreign)", agent_id)
            await websocket.close(code=WS_CLOSE_UNKNOWN_AGENT)
            return
        channels = agent_config.get("channels", ["voice"]) if isinstance(agent_config, dict) else ["voice"]
        if "voice" not in channels:
            # Authorized principal, wrong-channel agent: same shape as
            # unknown-or-foreign (no oracle, no new codes in Phase A).
            logger.warning("voice ws denied for agent %s (non-voice channels)", agent_id)
            await websocket.close(code=WS_CLOSE_UNKNOWN_AGENT)
            return
        try:
            # Spec 0048 (Slice B): the execution record lands in the container's
            # repository-backed platform store (quickstart used to pass its own).
            # The playground marks its leg with `?leg=browser`: like the legacy
            # handler, forward it as the engine's browser-leg flag so a
            # telephony-configured agent still binds default IO handlers here.
            is_web_based_call = websocket.query_params.get(WS_LEG_PARAM) == WS_LEG_BROWSER_VALUE
            await service.run_call(
                agent_config=agent_config,
                ws=websocket,
                agent_id=agent_id,
                is_web_based_call=is_web_based_call,
                platform_store=container.platform_store(),
            )
        finally:
            # Channel lifecycle event (spec 0021, M2): the run's record joins
            # the tenant-stamped audit trail whether the run succeeded or not.
            await auth.audit(
                "call_recorded",
                user_id=principal.user_id,
                email=principal.email,
                detail=agent_id,
            )
            try:
                await websocket.close()
            except RuntimeError:
                pass  # the run or the client already closed the socket; a second close only spams logs



@router.post(PLACE_CALL_PATH, status_code=202, response_model=PlacedCall)
@inject
async def place_call(
    payload: PlaceCallRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Place one outbound call (spec 0008)."""
    principal = await _require_scope(request, auth, "calls:write")

    try:
        placed = await service.place_call(payload=payload)
    except AppError as exc:
        return error_response(exc)
    # Channel lifecycle event (spec 0021, M2): the dial joins the tenant-stamped
    # audit trail. Advisory by contract — `audit` never fails the placement.
    await auth.audit(
        "call_placed",
        user_id=principal.user_id,
        email=principal.email,
        detail=placed.execution_id,
    )
    return success_response(placed.model_dump(mode="json"), status_code=202)


@router.post(PARTNERS_PATH, status_code=201, response_model=TalkoPartnerView)
@inject
async def create_talko_partner(
    payload: CreateTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Store one partner credential record (spec 0008)."""
    await _require_scope(request, auth, "platform:write")

    try:
        view = await service.create_partner(payload=payload)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"), status_code=201)


@router.get(PARTNERS_PATH, response_model=TalkoPartnerListResponse)
@inject
async def list_talko_partners(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List partner records without secrets (spec 0008)."""
    await _require_scope(request, auth, "platform:read")

    try:
        views = await service.list_partners()
    except AppError as exc:
        return error_response(exc)
    return success_response(TalkoPartnerListResponse(partners=views).model_dump(mode="json"))


@router.get(PARTNER_ITEM_PATH, response_model=TalkoPartnerView)
@inject
async def get_talko_partner(
    partner_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Read one partner record without its secret (spec 0008)."""
    await _require_scope(request, auth, "platform:read")

    try:
        view = await service.get_partner(partner_id=partner_id)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"))


@router.put(PARTNER_ITEM_PATH, response_model=TalkoPartnerView)
@inject
async def update_talko_partner(
    partner_id: str,
    payload: UpdateTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Patch one partner record; empty key keeps the secret (spec 0008)."""
    await _require_scope(request, auth, "platform:write")

    try:
        view = await service.update_partner(partner_id=partner_id, payload=payload)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"))


@router.delete(PARTNER_ITEM_PATH)
@inject
async def delete_talko_partner(
    partner_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Soft-delete one partner record (spec 0008)."""
    await _require_scope(request, auth, "platform:write")

    try:
        await service.delete_partner(partner_id=partner_id)
    except AppError as exc:
        return error_response(exc)
    return success_response({"deleted": True})


@router.post(PARTNERS_PREVIEW_PATH, response_model=TalkoPartnerPreview)
@inject
async def preview_talko_partner(
    payload: ConnectTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Validate a partner key and preview its DIDs without persisting (spec 0009)."""
    await _require_scope(request, auth, "platform:write")
    try:
        preview = await service.preview_partner(talko_api_key=payload.talko_api_key)
    except AppError as exc:
        return error_response(exc)
    return success_response(preview.model_dump(mode="json"))


@router.post(PARTNERS_CONNECT_PATH, status_code=201, response_model=TalkoPartnerView)
@inject
async def connect_talko_partner(
    payload: ConnectTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Fetch-and-store a partner in one step for the connect UI (spec 0009)."""
    await _require_scope(request, auth, "platform:write")
    try:
        view = await service.connect_partner(payload=payload)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"), status_code=201)


@router.post(PARTNER_REFRESH_PATH, response_model=TalkoPartnerView)
@inject
async def refresh_talko_partner(
    partner_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Re-fetch a stored partner's DIDs with its own key (spec 0009)."""
    await _require_scope(request, auth, "platform:write")
    try:
        view = await service.refresh_partner_dids(partner_id=partner_id)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"))


# --- Inbound Twilio webhook (spec 0047, Slice B) --------------------------------------


def _twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute Twilio's ``X-Twilio-Signature``: base64(HMAC-SHA1(token, url + sorted k+v))."""
    payload = url + "".join(params[key] for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _valid_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    """Check the Twilio signature with a constant-time compare (no oracle).

    An empty token or signature never validates; a non-ASCII signature converts to
    ``False`` (the caller answers the identical reject — the conversion Rule 8 needs).
    """
    if not auth_token or not signature:
        return False
    expected = _twilio_signature(auth_token, url, params)
    try:
        return hmac.compare_digest(expected, signature)
    except (TypeError, ValueError):
        return False


def _twilio_auth_token(environment: Environment) -> str:
    """Read the Twilio account auth token from the declared field (Rule 4).

    Empty fails closed downstream (an empty token never validates); the token
    itself is never logged.
    """
    return str(environment.twilio_auth_token or "")


def _twilio_form_params(raw: bytes) -> dict[str, str]:
    """Decode the urlencoded Twilio body into plain params (no multipart dependency)."""
    return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))


def _inbound_store(request: Request) -> Any:  # why: the seam is a duck-typed read port
    """Resolve the phone-assignment store seam for the inbound lookup.

    A store staged on app state (tests) wins; otherwise the container's
    ``inbound_store`` provider (spec 0048: the platform store adapter) resolves
    it. ``None`` means no seam at all, which rejects the call.
    """
    state = request.app.state
    store = getattr(state, "inbound_store", None)
    if store is not None:
        return store
    container = getattr(state, CONTAINER_STATE_ATTR, None)
    provider = getattr(container, "inbound_store", None) if container is not None else None
    return provider() if provider is not None else None


async def _lookup_inbound_agent(
    called: str | None, store: Any
) -> tuple[str, dict[str, Any]] | None:  # why: Slice A config is an open mapping
    """Resolve ``(agent_id, inbound-config-dict)`` via Slice A; ``None`` rejects identically.

    Slice A owns normalization (E.164), unknown/unassigned/unparseable → ``None``,
    and duplicate-assignment triage (fail-closed + loud log); this layer only
    short-circuits a missing store seam to the same ``None``.
    """
    if store is None:
        return None
    try:
        from voiceai.modules.voice.session.inbound import resolve_inbound
    except ImportError:  # half-landed peer file: fail closed, never 500
        logger.warning("inbound lookup unavailable (Slice A import failed)")
        return None
    return await resolve_inbound(called or "", store)


def _blocklist_entries(inbound_config: dict[str, Any]) -> list[str]:  # why: Slice A config is an open mapping
    """Read the blocklist rows off the Slice A config dict (persisted `blocklist` key)."""
    raw = inbound_config.get("blocklist")
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set, frozenset)):
        return [str(entry) for entry in raw]
    return [str(raw)]


def _is_caller_blocked(caller: str | None, inbound_config: dict[str, Any]) -> bool:
    """Screen the caller against the config blocklist via Slice C (E.164 both sides)."""
    import voiceai.modules.voice.static_methods as static_methods_mod

    return bool(static_methods_mod.is_blocklisted(caller, _blocklist_entries(inbound_config)))


def _inbound_greeting(inbound_config: dict[str, Any]) -> str | None:
    """Resolve the spoken greeting via Slice C precedence (set wins, unset → None)."""
    import voiceai.modules.voice.static_methods as static_methods_mod

    greeting = inbound_config.get("greeting")
    if not isinstance(greeting, str):
        greeting = None
    return static_methods_mod.resolve_greeting(greeting, None)


def _twiml_say(greeting: str | None) -> str:
    """Wrap the greeting in a Say verb, XML-escaped, with the neutral default fallback."""
    text = greeting if greeting and greeting.strip() else TWIML_DEFAULT_GREETING
    return TWIML_SAY_TEMPLATE.format(greeting=html.escape(text, quote=True))


def _twiml_response(body: str) -> Response:
    """Wrap TwiML in its XML response (carrier contract — never the JSON envelope)."""
    return Response(content=body, media_type=TWIML_MEDIA_TYPE)


def _screen_inbound_call(caller: str | None, inbound_config: dict[str, Any], call_ref: str) -> None:
    """Run the non-blocking screening steps in contract order (spec 0047 Decision 3).

    Blocklist already decided upstream (reject); this runs spam verdict +
    caller-match enrichment for the record: both fail open by contract, so
    neither rejects here. Logs decision codes + identifiers only (never the
    caller number or enrichment payloads).
    """
    import voiceai.modules.voice.static_methods as static_methods_mod

    spam = static_methods_mod.spam_verdict(caller, bool(inbound_config.get("spam_protection", False)))
    match = static_methods_mod.caller_match_context(
        inbound_config.get("caller_match_source"),
        inbound_config.get("caller_match_ref"),
        caller,
    )
    logger.debug(
        "inbound screening (call %s): spam=%s match=%s", call_ref, spam.get("decision"), match.get("reason")
    )


async def _answer_inbound_twilio(request: Request, environment: Environment) -> Response:
    """Run the inbound pipeline and wrap the TwiML outcome (spec 0047 Slice B).

    Signature → Slice A lookup → screening (blocklist reject; spam + match
    recorded, fail-open) → Say or Reject. Every rejection (bad signature,
    unknown number, blocked caller, unexpected failure) answers the identical
    reject body so dialing discloses nothing.
    """
    try:
        params = _twilio_form_params(await request.body())
        url = str(request.url)
        call_sid = params.get("CallSid", "")
        signature = request.headers.get(TWILIO_SIGNATURE_HEADER)
        token = _twilio_auth_token(environment)
        if not _valid_twilio_signature(token, url, params, signature):
            logger.warning("inbound twilio rejected: bad signature")
            return _twiml_response(TWIML_REJECT)
        call_ref = call_sid if call_sid.isalnum() else "unknown"
        resolved = await _lookup_inbound_agent(params.get("To"), _inbound_store(request))
        if resolved is None:
            logger.warning("inbound twilio rejected: unknown number (call %s)", call_ref)
            return _twiml_response(TWIML_REJECT)
        agent_id, inbound_config = resolved
        if _is_caller_blocked(params.get("From"), inbound_config):
            logger.warning("inbound twilio rejected: blocked caller (call %s)", call_ref)
            return _twiml_response(TWIML_REJECT)
        _screen_inbound_call(params.get("From"), inbound_config, call_ref)
        logger.debug("inbound twilio accepted for agent %s (call %s)", agent_id, call_ref)
        return _twiml_response(_twiml_say(_inbound_greeting(inbound_config)))
    except Exception as exc:  # fail closed: log the kind, answer the identical reject
        logger.warning("inbound twilio failed closed (%s)", type(exc).__name__)
        return _twiml_response(TWIML_REJECT)


@router.post(INBOUND_TWILIO_PATH)
@inject
async def inbound_twilio(request: Request, environment: EnvironmentDep) -> Response:
    """Answer one Twilio inbound call: verify, look up, screen, then TwiML (spec 0047 Slice B).

    Thin layer only (Rule 1f): no agent logic, no repository access — the Slice A/B
    seams do the work. Carrier webhooks are unauthenticated by design (Decision 1);
    authenticity comes from the Twilio signature, and every denial answers
    byte-identically (Decisions 1 and 6 — no oracle).
    """
    return await _answer_inbound_twilio(request, environment)
