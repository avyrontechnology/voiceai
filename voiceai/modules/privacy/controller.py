"""HTTP surface for the privacy module: wiring only, no logic (AGENTS.md rule 1f)."""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from voiceai.common.constants import HTTP_CREATED
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, ensure_permitted, request_principal
from voiceai.modules.privacy import constants as C
from voiceai.modules.privacy.schemas import ConsentView, GrantConsentPayload
from voiceai.modules.privacy.service import PrivacyService

router = APIRouter(prefix=C.ROUTE_PREFIX, tags=[C.MODULE_NAME])

ServiceDep = Annotated[PrivacyService, Depends(Provide[VoiceAIContainer.privacy_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_scope(request: Request, auth: AuthService, scope: str) -> None:
    """Gate the caller on a scope, preferring the middleware-stashed principal.

    Args:
        request: The incoming request (cookie + bearer fallback, stashed principal).
        auth: The auth service resolving credentials the middleware did not.
        scope: The scope this route requires.

    Raises:
        InvalidCredentialsError: When no credential resolves (401 envelope).
        ForbiddenError: When the principal lacks `scope` (403 envelope).
    """
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get(C.AUTHORIZATION_HEADER, "")
    )
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")


@router.post(C.CONSENT_PATH, status_code=HTTP_CREATED)
@inject
async def grant_consent(
    payload: GrantConsentPayload, request: Request, auth: AuthServiceDep, service: ServiceDep
) -> JSONResponse:
    """Grant consent for one subject/purpose pair (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    client_ip = request.client.host if request.client else None
    record = await service.grant(
        subject_id=payload.subject_id,
        purpose=payload.purpose,
        policy_version=payload.policy_version,
        client_ip=client_ip,
    )
    return success_response(ConsentView.from_record(record).model_dump(), status_code=HTTP_CREATED)


@router.get(C.CONSENT_SUBJECT_PATH)
@inject
async def list_consents(subject_id: str, request: Request, auth: AuthServiceDep, service: ServiceDep) -> JSONResponse:
    """List active consents for a subject (`platform:read`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    records = await service.list_consents(subject_id=subject_id)
    return success_response([ConsentView.from_record(record).model_dump() for record in records])


@router.delete(C.CONSENT_WITHDRAW_PATH)
@inject
async def withdraw_consent(
    subject_id: str, purpose: str, request: Request, auth: AuthServiceDep, service: ServiceDep
) -> JSONResponse:
    """Withdraw consent via tombstone (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    record = await service.withdraw(subject_id=subject_id, purpose=purpose)
    return success_response(ConsentView.from_record(record).model_dump())
