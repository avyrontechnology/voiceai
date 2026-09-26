"""Chat runtime: turn-by-turn text conversations over persisted sessions (spec 0038).

All business logic, no HTTP types. The repository serves the tenant-scoped
session view the container binds per request; agent resolution goes through
the scoped definitions port (unknown-or-foreign reads as missing — no oracle);
each reply comes from the injected single-turn completion runner.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.chat import constants as C
from voiceai.modules.chat.errors import ChatChannelError, ChatError, ChatInvalidError, ChatNotFoundError
from voiceai.modules.chat.exceptions import ensure_agent_found, ensure_session_found
from voiceai.modules.chat.models import ChatSession
from voiceai.modules.chat.repository import ChatSessionsRepository
from voiceai.modules.chat.static_methods import build_system_text, cap_messages, extract_chat_model, to_llm_messages
from voiceai.modules.chat.utils import build_history_window, new_chat_message

__all__ = ["ChatLlmPort", "ChatService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


@runtime_checkable
class ChatLlmPort(Protocol):
    """Single-turn conversational completion (spec 0038, Phase C).

    The container wires `voiceai.modules.chat.adapters.llm.complete_chat_turn`
    here; it satisfies this protocol structurally (matching call signature),
    so the service never imports the LLM stack (AGENTS.md §3.1).
    """

    async def __call__(
        self,
        messages: list[dict[str, Any]],  # why: the LLM wire seam is free-form message dicts
        model: str | None = None,
    ) -> str:
        """Complete one turn for full inline history and return the assistant text.

        Args:
            messages: System prompt first, then alternating user/assistant turns.
            model: Provider model id, or `None` for the runner default.

        Returns:
            The assistant reply text.
        """
        ...


class ChatService:
    """Serve text-in/text-out conversations for chat-channel agents.

    Args:
        repository: The `chat_sessions` collection (tenant-scoped view — the
            container binds the request tenant, never the caller).
        definitions: The agents definition port for resolution and channel
            gating, or `None` — unwired compositions fail closed (404).
        complete: The single-turn completion runner (container wires
            `complete_chat_turn`; tests inject fakes).
    """

    def __init__(
        self,
        repository: ChatSessionsRepository,
        definitions: Any | None = None,  # why: AgentDefinitionPort without a cross-module import
        complete: ChatLlmPort | None = None,
    ) -> None:
        self._repository = repository
        self._definitions = definitions
        self._complete = complete

    async def post_message(
        self,
        session_id: str | None,
        agent_id: str,
        tenant_id: str,
        user_id: str | None,
        email: str | None,
        content: str,
    ) -> dict[str, str]:
        """Append one user turn, run the LLM, persist and return the reply.

        Load-or-create: a `None` session id mints a fresh session; a given id
        must resolve in this tenant AND belong to the addressed agent, else
        the no-oracle 404 (confused-deputy guard, spec 0038).

        Args:
            session_id: Resume key, or `None` to start a session.
            agent_id: The addressed agent.
            tenant_id: Request tenant (isolation is enforced by the scoped
                store; stamped explicitly here so the intent is visible).
            user_id: Acting user for the audit fields, when known.
            email: Caller email, carried for audit symmetry — never persisted.
            content: The user turn text.

        Returns:
            `{"session_id": ..., "reply": ...}`.

        Raises:
            ChatNotFoundError: Unknown/foreign agent, or session missing or
                owned by another agent.
            ChatChannelError: The agent exists but serves no chat channel.
            ChatInvalidError: The turn text is whitespace-only (no store
                write, no LLM call).
            pydantic.ValidationError: The turn text is empty (rejected at the
                model boundary before any store or LLM work).
            ChatError: The completion runner is unwired, timed out, or failed.
        """
        config = await self._resolve_agent(agent_id)
        self._ensure_chat_channel(config, agent_id)
        user_turn = new_chat_message(C.ROLE_USER, content)
        text = user_turn.content.strip()
        if not text:
            raise ChatInvalidError(
                C.BLANK_CONTENT_MESSAGE,
                details={"agent_id": agent_id},
            )
        session = await self._load_or_create_session(session_id, agent_id, tenant_id, user_id)
        session.messages.append(user_turn.model_copy(update={"content": text}))
        windowed = build_history_window(session.messages, C.HISTORY_WINDOW)
        llm_messages = to_llm_messages(windowed, build_system_text(config))
        reply = await self._complete_turn(llm_messages, extract_chat_model(config))
        session.messages.append(new_chat_message(C.ROLE_ASSISTANT, reply))
        session.messages = cap_messages(session.messages, C.MAX_HISTORY)
        session.touch(user_id)
        stored = await self._repository.save_session(session)
        logger.info("chat turn agent=%s session=%s turns=%d", agent_id, stored.session_id, len(stored.messages))
        return {"session_id": stored.session_id, "reply": reply}

    async def get_history(self, agent_id: str, tenant_id: str) -> list[ChatSession]:
        """List this tenant's sessions for one agent, oldest first, bounded.

        Tenant isolation is enforced by the scoped store bound at
        construction; `tenant_id` is carried so the controller's audit trail
        names the boundary every list crossed.

        Args:
            agent_id: The agent whose histories to list.
            tenant_id: Request tenant, for audit symmetry.

        Returns:
            The tenant's sessions for the agent (empty when none — unknown
            agents list as empty, never 404, so ids cannot be probed here).
        """
        logger.info("chat history agent=%s tenant=%s", agent_id, tenant_id)
        return list(await self._repository.list_sessions(agent_id))

    async def _resolve_agent(
        self,
        agent_id: str,
    ) -> dict[str, Any]:  # why: the engine seam is raw agent-config dicts
        """Resolve the agent config through the scoped definitions port.

        Args:
            agent_id: The addressed agent.

        Returns:
            The stored agent configuration.

        Raises:
            ChatNotFoundError: When no row resolved (unknown or foreign).
        """
        if self._definitions is None:
            logger.warning("chat agent check skipped: definitions unwired")
            stored = None
        else:
            stored = await self._definitions.get_agent(agent_id)
        return ensure_agent_found(stored, agent_id)

    def _ensure_chat_channel(
        self,
        config: dict[str, Any],  # why: the engine seam is raw agent-config dicts
        agent_id: str,
    ) -> None:
        """Reject posts to agents that serve no chat channel.

        Args:
            config: The resolved agent configuration.
            agent_id: The addressed agent (for the error details).

        Raises:
            ChatChannelError: When `chat` is not among the agent's channels.
        """
        channels = config.get(C.AGENT_CHANNELS_KEY, list(C.DEFAULT_CHANNELS))
        if not isinstance(channels, list) or C.CHAT_CHANNEL not in channels:
            raise ChatChannelError(
                C.CHANNEL_MISMATCH_MESSAGE,
                details={"agent_id": agent_id},
            )

    async def _load_or_create_session(
        self, session_id: str | None, agent_id: str, tenant_id: str, user_id: str | None
    ) -> ChatSession:
        """Resume a tenant-owned session for this agent, or mint a fresh one.

        Args:
            session_id: Resume key, or `None` to start a session.
            agent_id: The addressed agent (must own a resumed session).
            tenant_id: Request tenant, stamped on minted rows.
            user_id: Acting user for the audit fields, when known.

        Returns:
            The resumed or minted session.

        Raises:
            ChatNotFoundError: A given id resolved to nothing, or to a
                session owned by another agent.
        """
        if session_id is None:
            return ChatSession(
                session_id=new_id(C.SESSION_ID_PREFIX),
                agent_id=agent_id,
                tenant_id=tenant_id,
                created_by=user_id,
                updated_by=user_id,
            )
        session = ensure_session_found(await self._repository.get_session(session_id), session_id)
        if session.agent_id != agent_id:
            raise ChatNotFoundError(
                C.SESSION_UNKNOWN_MESSAGE,
                details={"session_id": session_id},
            )
        return session

    async def _complete_turn(self, messages: list[dict[str, str]], model: str | None) -> str:
        """Run one bounded completion turn through the injected runner.

        Args:
            messages: System prompt first, then the trailing history window.
            model: Provider model id, or `None` for the runner default.

        Returns:
            The assistant reply text.

        Raises:
            ChatError: The runner is unwired, timed out, or failed (opaque —
                runner internals never reach the client).
        """
        runner = self._complete
        if runner is None:
            raise ChatError(C.COMPLETION_UNWIRED_MESSAGE)
        try:
            return await asyncio.wait_for(runner(messages, model), C.CHAT_LLM_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except ChatError:
            raise
        except asyncio.TimeoutError as exc:
            raise ChatError(C.COMPLETION_TIMEOUT_MESSAGE, cause=exc) from exc
        except Exception as exc:
            err = ChatError(C.COMPLETION_FAILED_MESSAGE, cause=exc)
            logger.error("chat completion failed error_id=%s", err.error_id, exc_info=exc)
            raise err from exc
