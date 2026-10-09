"""HTTP surface for the privacy module: wiring only, no logic (AGENTS.md rule 1f)."""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from voiceai.common.constants import HTTP_CREATED
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, ensure_permitted, request_principal
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.privacy import constants as C
from voiceai.modules.privacy.schemas import (
    ConsentView,
    GrantConsentPayload,
    RectifySubjectPayload,
    SubjectDataView,
)
from voiceai.modules.privacy.service import PrivacyService

router = APIRouter(prefix=C.ROUTE_PREFIX, tags=[C.MODULE_NAME])

ServiceDep = Annotated[PrivacyService, Depends(Provide[VoiceAIContainer.privacy_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_scope(request: Request, auth: AuthService, scope: str) -> Principal:
    """Gate the caller on a scope, preferring the middleware-stashed principal.

    Args:
        request: The incoming request (cookie + bearer fallback, stashed principal).
        auth: The auth service resolving credentials the middleware did not.
        scope: The scope this route requires.

    Returns:
        The gated principal (subject-rights actions stamp the acting operator).

    Raises:
        InvalidCredentialsError: When no credential resolves (401 envelope).
        ForbiddenError: When the principal lacks `scope` (403 envelope).
    """
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get(C.AUTHORIZATION_HEADER, "")
    )
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")
    return principal


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


# --- data subject rights (spec 0072) ----------------------------------------------------


@router.get(C.SUBJECT_DATA_PATH)
@inject
async def access_subject_data(
    subject_id: str, request: Request, auth: AuthServiceDep, service: ServiceDep
) -> JSONResponse:
    """Return every record the tenant holds for a subject (`platform:read`, Art. 15)."""
    principal = await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    records = await service.access_data(subject_id=subject_id, actor_id=principal.user_id or "")
    view = SubjectDataView(subject_id=subject_id, consent=[ConsentView.from_record(r) for r in records])
    return success_response(view.model_dump())


@router.delete(C.SUBJECT_PATH)
@inject
async def erase_subject(subject_id: str, request: Request, auth: AuthServiceDep, service: ServiceDep) -> JSONResponse:
    """Soft-erase every consent row of a subject (`platform:write`, Art. 17)."""
    principal = await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    erased = await service.erase_subject(subject_id=subject_id, actor_id=principal.user_id or "")
    return success_response({C.ERASED_FIELD: True, C.RECORDS_FIELD: erased})


@router.patch(C.SUBJECT_PATH)
@inject
async def rectify_subject(
    subject_id: str, payload: RectifySubjectPayload, request: Request, auth: AuthServiceDep, service: ServiceDep
) -> JSONResponse:
    """Rectify one purpose for a subject (`platform:write`, Art. 16)."""
    principal = await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    record = await service.rectify_subject(
        subject_id=subject_id,
        purpose=payload.purpose,
        policy_version=payload.policy_version,
        actor_id=principal.user_id or "",
    )
    return success_response(ConsentView.from_record(record).model_dump())
