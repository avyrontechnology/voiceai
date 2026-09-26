"""HTTP+SSE surface for the chat module: wiring only, no logic (AGENTS.md rule 1f).

`POST /chat/{agent_id}` appends one turn and streams the reply as
`text/event-stream` (`data: <slice>` frames plus terminal `data: [DONE]`);
`GET /chat/sessions?agent_id=` lists the tenant's bounded histories.
Authentication resolves through the container `AuthService` with no extra
scope beyond authenticated in v1 (spec 0038) — tenant binding comes from the
principal's org, never from client-supplied identity fields.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from voiceai.common.errors import AppError
from voiceai.common.responses import error_response, success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.chat import constants as C
from voiceai.modules.chat.helpers import wire_chat_history
from voiceai.modules.chat.schemas import PostChatBody
from voiceai.modules.chat.service import ChatService

__all__ = ["router"]

router = APIRouter(tags=[C.CHAT_TAG])

ServiceDep = Annotated[ChatService, Depends(Provide[VoiceAIContainer.chat_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_caller(request: Request, auth: AuthService) -> Principal:
    """Resolve the caller, preferring the middleware-stashed principal (one store trip).

    Args:
        request: The incoming request.
        auth: The container auth service for stacks without the middleware.

    Returns:
        The authenticated caller (anonymous callers fail with 401 inside `authenticate`).

    Raises:
        ForbiddenError: When the principal carries no bindable tenant — the
            scoped session store below would reject an empty tenant, so the
            gate fails here with a client error instead.
    """
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get("authorization", "")
    )
    ensure_permitted(bool(principal.org_id), C.AUTH_REQUIRED_MESSAGE)
    return principal


async def _reply_frames(reply: str) -> AsyncIterator[str]:
    """Yield one `data:` frame per reply slice plus the terminal `[DONE]`.

    Args:
        reply: The assistant text (slices concatenate back to it exactly).

    Yields:
        SSE frames, each terminated by a blank line.
    """
    for index in range(0, len(reply), C.SSE_CHUNK_SIZE):
        yield f"{C.SSE_DATA_PREFIX} {reply[index : index + C.SSE_CHUNK_SIZE]}\n\n"
    yield f"{C.SSE_DATA_PREFIX} {C.SSE_DONE_MARKER}\n\n"


@router.post(C.CHAT_POST_PATH)
@inject
async def post_chat_message(
    agent_id: str,
    payload: PostChatBody,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> Response:
    """Post one chat turn: the reply streams as SSE frames plus terminal `[DONE]`.

    The resumed/minted session id rides the `x-session-id` response header, so
    the client can continue the conversation on the next turn.
    """
    principal = await _require_caller(request, auth)
    try:
        result = await service.post_message(
            session_id=payload.session_id,
            agent_id=agent_id,
            tenant_id=principal.org_id,
            user_id=principal.user_id,
            email=principal.email,
            content=payload.message,
        )
    except AppError as exc:
        return error_response(exc)
    return StreamingResponse(
        _reply_frames(result["reply"]),
        media_type=C.SSE_MEDIA_TYPE,
        headers={C.SESSION_ID_HEADER: result["session_id"]},
    )

@router.get(C.CHAT_SESSIONS_PATH)
@inject
async def list_chat_sessions(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    agent_id: Annotated[str, Query(min_length=1, description="Agent whose histories to list.")],
) -> JSONResponse:
    """List this tenant's sessions for one agent, oldest first (bounded)."""
    principal = await _require_caller(request, auth)
    try:
        sessions = await service.get_history(agent_id, principal.org_id)
    except AppError as exc:
        return error_response(exc)
    return success_response(wire_chat_history(sessions))
