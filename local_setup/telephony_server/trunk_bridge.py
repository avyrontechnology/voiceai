"""Carrier-leg bridge from the example trunks to the single app (spec 0054).

The single app serves realtime media at ``WS /api/v1/chat/v1/{agent_id}`` and
closes every socket that does not redeem a single-use ``?ticket=`` (spec 0021).
A carrier cannot present that ticket itself: Twilio drops query strings on
``<Stream url>``, and a ticket lives 60 seconds while a phone may ring longer.
So the trunk terminates the carrier media socket on its own public tunnel and
relays it to the app over the private network:

    POST /call                      -> ``PendingDials.register`` -> ``call_ref``
    carrier answer callback         -> ``<Stream>`` at ``media_url(public, call_ref)``
    carrier WS /media/{call_ref}    -> ``relay_media``: claim the reference once,
                                       mint a ticket with the trunk's API key,
                                       open ``chat_url(...)`` and pump frames

The ticket stays on the private hop (never in carrier XML, never logged); the
carrier leg authenticates with the unguessable single-use ``call_ref``. The call
runs in the tenant of ``VOICEAI_API_KEY``'s owner — the app resolves the agent
inside that tenant only.

Shared by ``twilio_api_server`` and ``plivo_api_server`` (uvicorn's ``--app-dir``
puts this directory on ``sys.path``). Standalone on purpose: it speaks the app's
HTTP contract and imports nothing from ``voiceai``;
``tests/test_trunk_stream_url.py`` pins the paths below to the app's constants.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode, urlsplit

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

__all__ = [
    "BridgeSettings",
    "CarrierRejectedError",
    "PendingDial",
    "PendingDials",
    "TicketMintError",
    "TrunkBusyError",
    "TrunkError",
    "TrunkNotConfiguredError",
    "TunnelUnavailableError",
    "UnknownCallError",
    "answer_url",
    "chat_url",
    "install_error_handler",
    "media_url",
    "mint_ticket",
    "relay_media",
    "resolve_public_url",
]

logger = logging.getLogger("trunk.bridge")

# --- The single app's contract (spec 0021 / 0048; pinned by tests/test_trunk_stream_url.py) ---
#: Mint route: any principal with ``calls:write`` (here: the trunk's API key).
WS_TICKET_PATH = "/api/v1/auth/ws-ticket"
#: The realtime call route.
CHAT_WS_PATH = "/api/v1/chat/v1/{agent_id}"
#: Query parameter carrying the single-use ticket.
WS_TICKET_PARAM = "ticket"
#: Success-envelope keys of the mint answer: ``{"ok": true, "data": {"ticket": ...}}``.
ENVELOPE_DATA_KEY = "data"
TICKET_KEY = "ticket"

# --- Trunk surface ---
#: The carrier media socket on the trunk; the reference rides the path because
#: Twilio drops query strings on ``<Stream url>``.
MEDIA_WS_PATH = "/media/{call_ref}"
#: Query parameter of the answer callback naming the pending dial.
CALL_REF_PARAM = "call_ref"

# --- Environment ---
ENV_API_KEY = "VOICEAI_API_KEY"
ENV_INTERNAL_URL = "VOICEAI_INTERNAL_URL"
DEFAULT_INTERNAL_URL = "http://voiceai-app:5001"
#: The ngrok agent API inside the compose network.
NGROK_TUNNELS_URL = "http://ngrok:4040/api/tunnels"

# --- Bounds (seconds unless named otherwise) ---
NGROK_TIMEOUT_S = 5.0
TICKET_TIMEOUT_S = 10.0
APP_CONNECT_TIMEOUT_S = 10.0
RELAY_SEND_TIMEOUT_S = 5.0
RELAY_MAX_FRAME_BYTES = 4 * 1024 * 1024
#: A dial waits this long for its answer (carrier ring timeouts are far shorter).
PENDING_DIAL_TTL_S = 300.0
#: Unanswered dials held at once; a full registry refuses new dials (503).
PENDING_DIAL_CAPACITY = 256
#: Entropy of a call reference (256 bit).
CALL_REF_BYTES = 32

# --- Websocket close codes ---
WS_CLOSE_POLICY = 1008
WS_CLOSE_UPSTREAM = 1011
#: App-side close codes that are a normal end of the leg (anything else is logged).
_NORMAL_CLOSE_CODES = frozenset({1000, 1001})

_WS_SCHEMES = {"http": "ws", "https": "wss"}
_HTTP_OK = 200


class TrunkError(Exception):
    """A trunk failure with a fixed, client-safe message (never exception text)."""

    status_code = 503
    detail = "Trunk unavailable."


class TrunkNotConfiguredError(TrunkError):
    """The trunk has no API key, or its app URL is not http(s)."""

    detail = f"Trunk is not configured: set {ENV_API_KEY} and {ENV_INTERNAL_URL}."


class TunnelUnavailableError(TrunkError):
    """The trunk's public tunnel cannot be resolved from the ngrok agent."""

    def __init__(self, tunnel_name: str) -> None:
        super().__init__(tunnel_name)
        self.detail = f"Public tunnel '{tunnel_name}' is not available."


class TrunkBusyError(TrunkError):
    """Too many dials are waiting for an answer."""

    detail = "Too many calls are waiting for an answer."


class CarrierRejectedError(TrunkError):
    """The carrier refused the dial."""

    status_code = 502
    detail = "Carrier rejected the dial."


class UnknownCallError(TrunkError):
    """The answer callback named no pending dial."""

    status_code = 404
    detail = "Unknown call."


class TicketMintError(TrunkError):
    """The app did not mint a ticket (unreachable, denied, or malformed answer)."""

    status_code = 502
    detail = "Agent bridge unavailable."


def install_error_handler(app: FastAPI) -> None:
    """Render every ``TrunkError`` as ``{"detail": <fixed message>}`` with its status."""

    async def _render(_request: Request, exc: Exception) -> JSONResponse:
        status = exc.status_code if isinstance(exc, TrunkError) else TrunkError.status_code
        detail = exc.detail if isinstance(exc, TrunkError) else TrunkError.detail
        return JSONResponse(status_code=status, content={"detail": detail})

    app.add_exception_handler(TrunkError, _render)


def _ws_base(http_url: str) -> str:
    """Map an http(s) base URL to its ws(s) twin, without a trailing slash.

    Raises:
        ValueError: The URL is not an absolute http(s) URL.
    """
    parts = urlsplit(http_url.strip())
    scheme = _WS_SCHEMES.get(parts.scheme.lower())
    if scheme is None or not parts.netloc:
        raise ValueError("not an absolute http(s) URL")
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}"


@dataclass(frozen=True)
class BridgeSettings:
    """What a trunk needs to reach the single app.

    Attributes:
        api_key: API key whose owner's role carries ``calls:write``; its tenant
            is the tenant every bridged call runs in.
        internal_url: The app as reachable from the trunk process (http or https).
    """

    api_key: str
    internal_url: str

    @classmethod
    def from_env(cls) -> BridgeSettings:
        """Read ``VOICEAI_API_KEY`` and ``VOICEAI_INTERNAL_URL`` from the environment."""
        return cls(
            api_key=os.getenv(ENV_API_KEY, "").strip(),
            internal_url=(os.getenv(ENV_INTERNAL_URL, "").strip() or DEFAULT_INTERNAL_URL).rstrip("/"),
        )

    def require_configured(self) -> None:
        """Fail before any dial or mint when the trunk cannot authenticate.

        Raises:
            TrunkNotConfiguredError: Empty key, or an app URL that is not http(s).
        """
        if not self.api_key:
            raise TrunkNotConfiguredError()
        try:
            _ws_base(self.internal_url)
        except ValueError as exc:
            raise TrunkNotConfiguredError() from exc


def chat_url(internal_url: str, agent_id: str, ticket: str) -> str:
    """Build the app's ticketed call socket URL.

    Args:
        internal_url: The app's http(s) base URL.
        agent_id: Agent to run; percent-encoded so it cannot add path or query parts.
        ticket: Single-use ticket from ``mint_ticket``.

    Returns:
        ``ws(s)://<app>/api/v1/chat/v1/<agent_id>?ticket=<ticket>``.

    Raises:
        ValueError: ``internal_url`` is not an absolute http(s) URL.
    """
    path = CHAT_WS_PATH.format(agent_id=quote(agent_id, safe=""))
    return f"{_ws_base(internal_url)}{path}?{urlencode({WS_TICKET_PARAM: ticket})}"


def media_url(public_url: str, call_ref: str) -> str:
    """Build the carrier ``<Stream>`` target on the trunk's own tunnel (no query string).

    Raises:
        ValueError: ``public_url`` is not an absolute http(s) URL.
    """
    return f"{_ws_base(public_url)}{MEDIA_WS_PATH.format(call_ref=quote(call_ref, safe=''))}"


def answer_url(public_url: str, path: str, call_ref: str) -> str:
    """Build the carrier answer-callback URL: the reference is its only parameter."""
    return f"{public_url.rstrip('/')}{path}?{urlencode({CALL_REF_PARAM: call_ref})}"


def resolve_public_url(tunnel_name: str) -> str:
    """Look the trunk's public base URL up by tunnel name on the ngrok agent.

    Args:
        tunnel_name: A tunnel defined in ``local_setup/ngrok-config.yml``.

    Returns:
        The tunnel's public http(s) URL (https preferred), without a trailing slash.

    Raises:
        TunnelUnavailableError: The agent is unreachable, answers garbage, or
            defines no such tunnel.
    """
    try:
        response = httpx.get(NGROK_TUNNELS_URL, timeout=NGROK_TIMEOUT_S)
        tunnels = response.json().get("tunnels", []) if response.status_code == _HTTP_OK else []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning("ngrok agent lookup failed (%s)", type(exc).__name__)
        raise TunnelUnavailableError(tunnel_name) from exc
    candidates = [
        str(tunnel.get("public_url", "")).rstrip("/")
        for tunnel in tunnels
        if isinstance(tunnel, dict) and tunnel.get("name") == tunnel_name
    ]
    for scheme in ("https://", "http://"):
        for candidate in candidates:
            if candidate.startswith(scheme):
                return candidate
    logger.warning("ngrok tunnel %s is not defined or not up", tunnel_name)
    raise TunnelUnavailableError(tunnel_name)


async def mint_ticket(settings: BridgeSettings) -> str:
    """Mint one single-use websocket ticket with the trunk's API key.

    Args:
        settings: The trunk's app URL and API key.

    Returns:
        The ticket (redeemable once, within its 60 second lifetime).

    Raises:
        TrunkNotConfiguredError: The trunk has no usable key or URL.
        TicketMintError: The app is unreachable, refuses the key, or answers
            without a ticket. The cause is logged by type only.
    """
    settings.require_configured()
    try:
        async with httpx.AsyncClient(timeout=TICKET_TIMEOUT_S) as client:
            response = await client.post(
                f"{settings.internal_url}{WS_TICKET_PATH}",
                headers={"Authorization": f"Bearer {settings.api_key}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("ticket mint failed (%s)", type(exc).__name__)
        raise TicketMintError() from exc
    if response.status_code != _HTTP_OK:
        logger.warning("ticket mint refused (HTTP %s)", response.status_code)
        raise TicketMintError()
    try:
        ticket = response.json()[ENVELOPE_DATA_KEY][TICKET_KEY]
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("ticket mint answered without a ticket (%s)", type(exc).__name__)
        raise TicketMintError() from exc
    if not isinstance(ticket, str) or not ticket:
        logger.warning("ticket mint answered without a ticket")
        raise TicketMintError()
    return ticket


@dataclass(frozen=True)
class PendingDial:
    """One dial waiting for its carrier leg.

    Attributes:
        agent_id: Agent the answered call is bridged to.
        public_url: The trunk's public base URL at dial time (the ``<Stream>`` host).
        expires_at: Clock reading after which the dial is forgotten.
    """

    agent_id: str
    public_url: str
    expires_at: float


class PendingDials:
    """In-process registry of dials waiting for an answer, keyed by ``call_ref``.

    A reference is unguessable, expires, and is claimed exactly once by the
    media socket. Thread-safe: ``/call`` runs in the threadpool, the relay on
    the event loop. One registry per trunk process (single uvicorn worker).

    Args:
        ttl_s: Seconds a dial waits for its answer.
        capacity: Dials held at once; ``register`` refuses beyond it.
        clock: Monotonic time source (injected by tests).
    """

    def __init__(
        self,
        *,
        ttl_s: float = PENDING_DIAL_TTL_S,
        capacity: int = PENDING_DIAL_CAPACITY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._capacity = capacity
        self._clock = clock
        self._dials: dict[str, PendingDial] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._dials)

    def _purge(self) -> None:
        """Drop expired dials (caller holds the lock)."""
        now = self._clock()
        for call_ref in [ref for ref, dial in self._dials.items() if dial.expires_at <= now]:
            del self._dials[call_ref]

    def register(self, agent_id: str, public_url: str) -> str:
        """Remember one dial and return its fresh reference.

        Raises:
            TrunkBusyError: The registry is full of unanswered dials.
        """
        with self._lock:
            self._purge()
            if len(self._dials) >= self._capacity:
                raise TrunkBusyError()
            call_ref = secrets.token_urlsafe(CALL_REF_BYTES)
            self._dials[call_ref] = PendingDial(
                agent_id=agent_id,
                public_url=public_url,
                expires_at=self._clock() + self._ttl_s,
            )
            return call_ref

    def peek(self, call_ref: str) -> PendingDial | None:
        """Return the pending dial without consuming it (the answer callback)."""
        with self._lock:
            self._purge()
            return self._dials.get(call_ref)

    def claim(self, call_ref: str) -> PendingDial | None:
        """Consume the pending dial: a second claim of the same reference finds nothing."""
        with self._lock:
            self._purge()
            return self._dials.pop(call_ref, None)


#: Opens the app-side socket for one URL; tests inject a fake.
AppConnector = Callable[[str], Awaitable[Any]]


async def _open_app_socket(url: str) -> Any:  # why: the client connection type is library-private
    """Open the app's call socket (bounded; no client pings — the carrier leg owns liveness)."""
    return await websockets.connect(
        url,
        open_timeout=APP_CONNECT_TIMEOUT_S,
        ping_interval=None,
        max_size=RELAY_MAX_FRAME_BYTES,
    )


async def _carrier_to_app(carrier: WebSocket, upstream: Any) -> None:
    """Forward carrier frames verbatim until the carrier disconnects."""
    while True:
        message = await carrier.receive()
        if message.get("type") == "websocket.disconnect":
            return
        frame = message.get("text")
        if frame is None:
            frame = message.get("bytes")
        if frame is not None:
            await asyncio.wait_for(upstream.send(frame), RELAY_SEND_TIMEOUT_S)


async def _app_to_carrier(carrier: WebSocket, upstream: Any) -> None:
    """Forward app frames verbatim until the app closes its side."""
    async for frame in upstream:
        if isinstance(frame, str):
            await asyncio.wait_for(carrier.send_text(frame), RELAY_SEND_TIMEOUT_S)
        else:
            await asyncio.wait_for(carrier.send_bytes(frame), RELAY_SEND_TIMEOUT_S)


async def _pump(carrier: WebSocket, upstream: Any) -> None:
    """Run both directions until either side ends, then stop the other."""
    tasks = (
        asyncio.create_task(_carrier_to_app(carrier, upstream), name="trunk-carrier-to-app"),
        asyncio.create_task(_app_to_carrier(carrier, upstream), name="trunk-app-to-carrier"),
    )
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception) and not isinstance(result, websockets.ConnectionClosed):
            logger.warning("media relay ended on %s", type(result).__name__)


async def _close_quietly(close: Callable[[], Awaitable[Any]]) -> None:
    """Close one side; an already-closed socket is not an error."""
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 - closing is best effort on a leg that already ended
        logger.debug("socket already closed (%s)", type(exc).__name__)


async def relay_media(
    carrier: WebSocket,
    call_ref: str,
    dials: PendingDials,
    settings: BridgeSettings,
    *,
    connector: AppConnector | None = None,
) -> None:
    """Bridge one carrier media socket to the agent on the single app.

    Claims the pending dial (single use), accepts the carrier, mints a ticket,
    opens the app socket with it and pumps frames both ways until either side
    ends. An unknown, expired or already-used reference is refused before the
    socket is accepted and never mints. Never raises for bridge failures: the
    carrier socket is closed with a code and the cause is logged by type.

    Args:
        carrier: The carrier's media websocket (not yet accepted).
        call_ref: Reference from the socket path.
        dials: The trunk's pending-dial registry.
        settings: The trunk's app URL and API key.
        connector: App-socket opener (tests); the real websocket client when ``None``.
    """
    dial = dials.claim(call_ref)
    if dial is None:
        logger.warning("media socket refused: unknown, expired or used call reference")
        await carrier.close(code=WS_CLOSE_POLICY)
        return
    await carrier.accept()
    try:
        ticket = await mint_ticket(settings)
        upstream = await (connector or _open_app_socket)(chat_url(settings.internal_url, dial.agent_id, ticket))
    except (TrunkError, OSError, asyncio.TimeoutError, ValueError, websockets.WebSocketException) as exc:
        logger.warning("bridge to agent %s failed (%s)", dial.agent_id, type(exc).__name__)
        await _close_quietly(lambda: carrier.close(code=WS_CLOSE_UPSTREAM))
        return
    logger.info("carrier leg bridged to agent %s", dial.agent_id)
    try:
        await _pump(carrier, upstream)
    finally:
        await _close_quietly(upstream.close)
        await _close_quietly(carrier.close)
        close_code = getattr(upstream, "close_code", None)
        if close_code is not None and close_code not in _NORMAL_CLOSE_CODES:
            # 4401 denied ticket, 4403 websocket dark, 4404 unknown or foreign agent.
            logger.warning("app closed the leg for agent %s with code %s", dial.agent_id, close_code)
        else:
            logger.info("carrier leg for agent %s ended", dial.agent_id)
