"""Engine-run bridges for the voice call service (AGENTS.md §3.1, bridge 1; spec 0004 B4).

`VoiceCallService` is legacy-free by construction: the two callables it needs — the
``AssistantManager`` constructor and the platform's execution recorder — are injected
through the container, and this adapter is where they actually touch legacy code.

Import weight: ``AssistantManager`` loads at module scope (it drags the full engine —
task_manager, providers), so importing THIS module is the heavy step. The voice
``register()`` therefore imports it only inside the container provider, and the provider
resolves at process composition time (quickstart module init), exactly when the legacy
server used to import ``AssistantManager`` directly. The platform hook import stays
INSIDE ``record_execution`` on purpose: the legacy WS handler imported it lazily in its
``finally`` so a broken/absent platform layer surfaces as a swallowed "Execution logging
skipped" warning, never a dead call path — that contract is preserved verbatim.

Retirement: both bridges die when the flagged WS controller (B14) and the platform
strangler (spec 0005+) replace the quickstart entry.
"""

from __future__ import annotations

from typing import Any

# §3.1 bridge import — retires at the B14 controller cutover.
from voiceai.agent_manager.assistant_manager import AssistantManager

__all__ = ["build_assistant_manager", "record_execution"]


def build_assistant_manager(
    agent_config: dict[str, Any],
    ws: Any,  # why: the engine treats the websocket opaquely; typing it would import HTTP into the seam
    assistant_id: Any,  # why: legacy accepts any identifier-ish value (str in practice)
    *,
    is_web_based_call: Any,  # why: raw truthiness flag, exactly as the legacy handler passed it
) -> AssistantManager:
    """Construct the legacy ``AssistantManager`` exactly as the quickstart WS handler did.

    Args:
        agent_config: The stored agent configuration (already browser-leg adjusted by
            the caller when applicable).
        ws: The live websocket the engine speaks over.
        assistant_id: The agent's id (positional in the legacy constructor).
        is_web_based_call: Browser-leg flag, forwarded as the same keyword.

    Returns:
        The manager whose ``run(local=True)`` drives the call.
    """
    return AssistantManager(agent_config, ws, assistant_id, is_web_based_call=is_web_based_call)


async def record_execution(
    store: Any,  # why: the platform store is optional and duck-typed at the hook boundary
    *,
    agent_id: str,
    run_id: Any,  # why: getattr-sourced legacy value, may be None
    history: list[dict[str, Any]],
    task_outputs: list[Any],  # why: engine task outputs are free-form legacy dicts
    direction: str,
    output: dict[str, Any] | None,
) -> None:
    """Persist one engine execution through the platform hook (best-effort contract).

    Mirrors the legacy handler's ``finally`` block: the import happens per call, so an
    unimportable platform layer raises HERE and the service's catch turns it into the
    same "Execution logging skipped" warning the quickstart server always logged.

    Args:
        store: The platform store from app state, or None.
        agent_id: The agent whose call finished.
        run_id: The engine run id, or None when the manager never exposed one.
        history: Prior conversation history (the WS path always passes ``[]``).
        task_outputs: Every task output the run yielded.
        direction: Call direction label (the WS path records "inbound").
        output: The last conversation payload carrying transcript/timings, or None.
    """
    from voiceai.platform.engine_hook import record_engine_execution

    await record_engine_execution(
        store,
        agent_id=agent_id,
        run_id=run_id,
        history=history,
        task_outputs=task_outputs,
        direction=direction,
        output=output,
    )
