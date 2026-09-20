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
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from voiceai.errors import ConfigurationError
from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import Execution, ExecutionStatus, new_id, utcnow
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

TRUNK_URL = os.getenv("TALKO_TRUNK_URL", "http://talko-app:8004").rstrip("/")
TRUNK_TIMEOUT_S = float(os.getenv("TALKO_TRUNK_TIMEOUT_S", "20"))
DIAL_CONCURRENCY = int(os.getenv("TALKO_DIAL_CONCURRENCY", "3"))


def _trunk_url(explicit: Optional[str] = None) -> str:
    """Trunk base URL resolved at call time, not import time.

    The engine imports this module before ``load_dotenv()`` runs, so the
    module-level ``TRUNK_URL`` snapshot misses ``.env`` values for local
    runs; compose injects env before import but operators can also change it
    without rebuilding. Precedence: per-request value > env > import default.
    NOTE: inside the compose network this must be ``http://talko-app:8004``
    (``localhost`` there is the engine container itself, where nothing
    listens — every dial fails instantly with "All connection attempts
    failed"). ``localhost:8004`` is only correct for host-local runs.
    """
    return (explicit or os.getenv("TALKO_TRUNK_URL") or TRUNK_URL).rstrip("/")


def _trunk_timeout_s() -> float:
    """Per-dial HTTP timeout resolved at call time (same late-env reason)."""
    try:
        return float(os.getenv("TALKO_TRUNK_TIMEOUT_S", str(TRUNK_TIMEOUT_S)))
    except ValueError:
        return TRUNK_TIMEOUT_S


def _trunk_headers() -> Dict[str, str]:
    """The trunk's dial endpoint requires X-API-Key once TELEPHONY_API_KEY is configured there.

    Read at call time (not import time) so tests and late `load_dotenv()` calls are honoured;
    the first configured key is the one this engine presents.
    """
    keys = [k.strip() for k in os.getenv("TELEPHONY_API_KEY", "").split(",") if k.strip()]
    return {"X-API-Key": keys[0]} if keys else {}


async def resolve_talko_partner_credentials(
    store: MemoryStore,
    *,
    partner_id: Optional[str],
    explicit_key: Optional[str] = None,
    explicit_did: Optional[str] = None,
    explicit_base: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve Talko credentials per partner from DB (not env).

    Precedence per field: explicit per-request value > partner DB record >
    None (caller falls back to trunk env defaults). A partner_id with no
    matching record is a 400 (fail closed — never dial on another partner's
    credentials). DIDs are normalized to digits-only (talko-service requires
    10-15 digits; callers paste E.164 with '+').
    """
    key, did, base = explicit_key, explicit_did, explicit_base
    if partner_id and (not key or not did or base is None):
        record = await store.get_talko_partner(partner_id)
        if record is None:
            raise ConfigurationError(
                "Unknown Talko partner '{}'. Create it via POST /talko/partners first.".format(partner_id),
                path="partner_id",
            )
        if not key:
            key = record.talko_api_key or None
        if not did:
            did = record.default_did
        if base is None:
            base = record.talko_api_base_url
    if did:
        did = re.sub(r"\D", "", did) or None
    return key, did, base


def validate_recipient_number(to_number: str) -> str:
    """Digits-only destination check (Tata rejects malformed numbers with an
    opaque BAD_REQUEST, so fail fast here with a clear 400).

    Generic bounds are 10-15 digits; Indian numbers (country code 91, which
    is all Tata Tele dials) must be 91 + 10 digits — an 11-digit 91… number
    is almost always a dropped-digit typo.
    """
    digits = re.sub(r"\D", "", to_number or "")
    if not 10 <= len(digits) <= 15:
        raise ConfigurationError(
            "to_number '{}' is not dialable: need 10-15 digits, got {}.".format(to_number, len(digits)),
            path="to_number",
        )
    if digits.startswith("91") and len(digits) != 12:
        raise ConfigurationError(
            "to_number '{}' looks like a truncated Indian mobile: need 91 + 10 digits.".format(to_number),
            path="to_number",
        )
    return digits


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
    partner_id: Optional[str] = None,
    talko_api_base_url: Optional[str] = None,
) -> Execution:
    """Dial one real call through the Talko trunk. Never raises for trunk errors."""
    api_key, caller_did, api_base = await resolve_talko_partner_credentials(
        store,
        partner_id=partner_id,
        explicit_key=talko_api_key,
        explicit_did=from_number,
        explicit_base=talko_api_base_url,
    )
    validate_recipient_number(to_number)
    execution = Execution(
        execution_id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        direction="outbound",
        to_number=to_number,
        from_number=caller_did,
        variables=variables or {},
    )
    await store.save_execution(execution)

    body: Dict[str, Any] = {"agent_id": agent_id, "recipient_phone_number": to_number}
    if caller_did:
        body["caller_did"] = caller_did
    if api_key:
        body["talko_api_key"] = api_key
    if partner_id:
        body["partner_id"] = partner_id
    if api_base:
        body["talko_api_base_url"] = api_base
    if variables:
        body["variables"] = dict(variables)
    url = "{}/talko/call".format(_trunk_url(trunk_url))
    try:
        # Only pass headers when a key is configured: keeps the call shape stable for callers
        # (and test doubles) that predate the trunk API key.
        request_kwargs: Dict[str, Any] = {"json": body}
        headers = _trunk_headers()
        if headers:
            request_kwargs["headers"] = headers
        async with httpx.AsyncClient(timeout=_trunk_timeout_s()) as client:
            resp = await client.post(url, **request_kwargs)
        if resp.status_code >= 400:
            raise RuntimeError("trunk rejected dial: {}".format(resp.text[:300]))
        execution.status = ExecutionStatus.RINGING
        await store.save_execution(execution)
        # Trunk accepted (async dial): media/answer tracking lives in Talko.
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
        execution.summary = "Dialed via Talko trunk; live on agent {}.".format(agent_id)
        execution.extracted_data = {"trunk": "talko", "trunk_response": trunk_payload}
    except Exception as e:
        logger.error("Talko dial failed trunk=%s to_number=%s: %s", url, to_number, e)
        execution.status = ExecutionStatus.FAILED
        execution.summary = "Talko dial failed (trunk {}): {}".format(url, e)
        execution.hangup_code = "failed"
        execution.ended_at = utcnow()
    await store.save_execution(execution)
    return execution
