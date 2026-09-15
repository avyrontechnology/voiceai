"""Real outbound dialing via the Talko trunk (Tata Tele).

Batches default to simulation (`provider="simulated"`). When a batch sets
`provider="talko"`, each entry is dialed for real by POSTing the Talko
telephony server, which asks talko-service to place the call and relays
the answered media back to this engine's `/chat/v1/{agent_id}` socket.

Execution lifecycle for trunk-dialed entries:
QUEUED -> RINGING (trunk accepted) -> IN_PROGRESS (PENDING_MEDIA), and the entry stays
IN_PROGRESS: call outcome (answer/hangup/transcript) lands in Talko's CDR,
not in this execution — there is no Talko->voiceai completion callback yet.
A future `POST /talko/cdr-callback` must flip IN_PROGRESS -> COMPLETED/FAILED/NO_ANSWER
and decrement the batch `pending` counter; until then batches report trunk-accepted dials
as `pending`, never `completed`. Trunk errors mark the entry FAILED with
the trunk's message in `summary` so batches degrade entry-by-entry.

Cancellation: `asyncio.CancelledError` (batch stop) always propagates so the batch
background task can stop without leaving orphan dials starting; every other error is
captured as FAILED. The trunk POST is bounded by with_timeout (classified
ProviderTimeoutError) in addition to the httpx timeout.

Env:
    TALKO_TRUNK_URL        e.g. http://talko-app:8004 (default)
    TALKO_TRUNK_TIMEOUT_S  per-dial HTTP timeout (default 20)
    TALKO_DIAL_CONCURRENCY max parallel dials per batch (default 3)
"""

import asyncio
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


def _trunk_headers() -> Dict[str, str]:
    """The trunk's dial endpoint requires X-API-Key once TELEPHONY_API_KEY is configured there.

    Read at call time (not import time) so tests and late `load_dotenv()` calls are honoured;
    the first configured key is the one this engine presents.
    """
    keys = [k.strip() for k in os.getenv("TELEPHONY_API_KEY", "").split(",") if k.strip()]
    return {"X-API-Key": keys[0]} if keys else {}


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
    org_id: str = "default",
) -> Execution:
    """Dial one real call through the Talko trunk. Never raises for trunk errors.

    `talko_api_key` is request-scoped (per-batch ephemeral or trunk TALKO_API_KEY env
    downstream); it is forwarded in the trunk body and never persisted or logged.
    Cancellation (batch stop) propagates; timeouts become FAILED with a classified cause.
    """
    from voiceai.errors import is_cancellation
    from voiceai.helpers.resilience import with_timeout

    execution = Execution(
        execution_id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        direction="outbound",
        to_number=to_number,
        from_number=from_number,
        variables=variables or {},
        org_id=org_id,
    )
    await store.save_execution(execution)

    body: Dict[str, Any] = {"agent_id": agent_id, "recipient_phone_number": to_number}
    if from_number:
        body["caller_did"] = from_number
    if talko_api_key:
        body["talko_api_key"] = talko_api_key
    url = "{}/talko/call".format((trunk_url or TRUNK_URL).rstrip("/"))
    try:
        # Only pass headers when a key is configured: keeps the call shape stable for callers
        # (and test doubles) that predate the trunk API key.
        request_kwargs: Dict[str, Any] = {"json": body}
        headers = _trunk_headers()
        if headers:
            request_kwargs["headers"] = headers

        async def _post() -> Any:
            async with httpx.AsyncClient(timeout=TRUNK_TIMEOUT_S) as client:
                return await client.post(url, **request_kwargs)

        resp = await with_timeout(
            _post(), TRUNK_TIMEOUT_S + 5.0, name="talko_trunk_post", component="telephony", provider="talko"
        )
        if resp.status_code >= 400:
            raise RuntimeError("trunk rejected dial: {}".format(resp.text[:300]))
        execution.status = ExecutionStatus.RINGING
        await store.save_execution(execution)
        # Trunk accepted (async dial): media/answer tracking lives in Talko CDR (PENDING_MEDIA).
        trunk_payload: Dict[str, Any] = {}
        try:
            trunk_payload = resp.json() if hasattr(resp, "json") else {}
        except Exception:
            trunk_payload = {}
        if not isinstance(trunk_payload, dict):
            trunk_payload = {}
        # Record enrichment only (no dial change): when the caller did not override
        # caller_did, the trunk resolved TALKO_AI_DID — persist it so history shows the DID.
        if not execution.from_number:
            did = trunk_payload.get("dedicated_did")
            if isinstance(did, str) and did.strip():
                execution.from_number = did.strip()
        execution.status = ExecutionStatus.IN_PROGRESS
        execution.summary = (
            "Dialed via Talko trunk; live on agent {}. PENDING_MEDIA: outcome in Talko CDR ".format(agent_id)
            + "(no Talko->voiceai completion callback yet)."
        )
        execution.extracted_data = {
            "trunk": "talko",
            "trunk_response": trunk_payload,
            "pending_media": True,
        }
    except asyncio.CancelledError:
        # Batch stop cancels the in-flight trunk POST: mark canceled and propagate so the
        # background batch task stops instead of counting this entry as failed/completed.
        execution.status = ExecutionStatus.CANCELED
        execution.summary = "Talko dial canceled: batch stopped."
        execution.hangup_code = "canceled"
        execution.ended_at = utcnow()
        await store.save_execution(execution)
        raise
    except Exception as e:
        if is_cancellation(e):
            raise
        logger.error("Talko dial failed to_number={}: {}".format(to_number, e))
        execution.status = ExecutionStatus.FAILED
        execution.summary = "Talko dial failed: {}".format(e)
        execution.hangup_code = "failed"
        execution.ended_at = utcnow()
    await store.save_execution(execution)
    return execution
