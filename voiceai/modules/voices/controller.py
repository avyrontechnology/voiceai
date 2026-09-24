"""HTTP surface for the voices module: wiring only, no logic (AGENTS.md rule 1f).

Legacy-identical routes and response keys (`platform/router.py::voices_router`
shapes), tenant-scoped and ownership-checked underneath. Scope gates mirror
the legacy surface (`platform:read` list, `platform:write` mutate) through the
voice-controller `_require_scope` precedent.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, ensure_permitted, request_principal
from voiceai.modules.voices import constants as C
from voiceai.modules.voices.helpers import wire_voice, wire_voice_list
from voiceai.modules.voices.schemas import CreateVoicePayload
from voiceai.modules.voices.service import VoicesService

__all__ = ["router"]

router = APIRouter(tags=[C.VOICES_TAG])

ServiceDep = Annotated[VoicesService, Depends(Provide[VoiceAIContainer.voices_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_scope(request: Request, auth: AuthService, scope: str) -> None:
    """Gate the caller on a scope, preferring the middleware-stashed principal."""
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get("authorization", "")
    )
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")


@router.get(C.VOICES_PATH)
@inject
async def list_voices(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    agent_id: str | None = None,
) -> JSONResponse:
    """List this tenant's voices, optionally for one agent (legacy shape)."""
    await _require_scope(request, auth, "platform:read")
    return success_response(wire_voice_list(await service.list_voices(agent_id)))


@router.post(C.VOICES_PATH, status_code=201)
@inject
async def create_voice(
    payload: CreateVoicePayload,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Store one custom voice (legacy shape, 201)."""
    await _require_scope(request, auth, "platform:write")
    voice = await service.create_voice(
        agent_id=payload.agent_id,
        name=payload.name,
        provider=payload.provider,
        provider_voice_id=payload.provider_voice_id,
        source=payload.source,
        language=payload.language,
    )
    return success_response(wire_voice(voice), status_code=201)


@router.delete(C.VOICE_ITEM_PATH)
@inject
async def delete_voice(
    voice_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Delete one voice; foreign ids read as missing (legacy shape)."""
    await _require_scope(request, auth, "platform:write")
    await service.delete_voice(voice_id)
    return success_response({"state": "deleted"})
