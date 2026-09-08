"""Real outbound dialing via the Talko trunk (Tata Tele).

Batches default to simulation (`provider="simulated"`). When a batch sets
`provider="talko"`, each entry is dialed for real by POSTing the Talko
telephony server, which asks talko-service to place the call and relays
the answered media back to this engine's `/chat/v1/{agent_id}` socket.

Execution lifecycle for trunk-dialed entries:
QUEUED -> RINGING (trunk accepted) -> IN_PROGRESS, and the entry stays
IN_PROGRESS: call outcome (answer/hangup/transcript) lands in Talko's CDR,
not in this execution — there is no Talko->voiceai completion callback yet
(see module docstring limitation). Trunk errors mark the entry FAILED with
the trunk's message in `summary` so batches degrade entry-by-entry.

Env:
    TALKO_TRUNK_URL        e.g. http://talko-app:8004 (default)
    TALKO_TRUNK_TIMEOUT_S  per-dial HTTP timeout (default 20)
    TALKO_DIAL_CONCURRENCY max parallel dials per batch (default 3)
"""

import os
from typing import Any, Dict, Optional

import httpx

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import Execution, ExecutionStatus, new_id, utcnow
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

TRUNK_URL = os.getenv("TALKO_TRUNK_URL", "http://talko-app:8004").rstrip("/")
TRUNK_TIMEOUT_S = float(os.getenv("TALKO_TRUNK_TIMEOUT_S", "20"))
DIAL_CONCURRENCY = int(os.getenv("TALKO_DIAL_CONCURRENCY", "3"))


async def dial_via_talko(
    store: MemoryStore,
    *,
    agent_id: str,
    to_number: str,
    from_number: Optional[str] = None,
    talko_api_key: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    batch_id: Optional[str] = None,
    trunk_url: Optional[str] = None,
) -> Execution:
    """Dial one real call through the Talko trunk. Never raises for trunk errors."""
    execution = Execution(
        execution_id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        to_number=to_number,
        from_number=from_number,
        variables=variables or {},
    )
    await store.save_execution(execution)

    body: Dict[str, Any] = {"agent_id": agent_id, "recipient_phone_number": to_number}
    if from_number:
        body["caller_did"] = from_number
    if talko_api_key:
        body["talko_api_key"] = talko_api_key
    url = "{}/talko/call".format((trunk_url or TRUNK_URL).rstrip("/"))
    try:
        async with httpx.AsyncClient(timeout=TRUNK_TIMEOUT_S) as client:
            resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            raise RuntimeError("trunk rejected dial: {}".format(resp.text[:300]))
        execution.status = ExecutionStatus.RINGING
        await store.save_execution(execution)
        # Trunk accepted (async dial): media/answer tracking lives in Talko.
        execution.status = ExecutionStatus.IN_PROGRESS
        execution.summary = "Dialed via Talko trunk; live on agent {}.".format(agent_id)
        execution.extracted_data = {"trunk": "talko", "trunk_response": resp.json()}
    except Exception as e:
        logger.error("Talko dial failed to_number={}: {}".format(to_number, e))
        execution.status = ExecutionStatus.FAILED
        execution.summary = "Talko dial failed: {}".format(e)
        execution.hangup_code = "failed"
        execution.ended_at = utcnow()
    await store.save_execution(execution)
    return execution
