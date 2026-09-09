"""Tell a socket that is actually gone from a send or receive that merely failed.

Input and output handlers share one rule: latch closed (or end the receive loop) only when the
WebSocket itself is dead. Every other failure is a per-packet problem that is logged and
skipped, so one bad frame cannot mute or deafen the rest of the call.
"""

from __future__ import annotations

from starlette.websockets import WebSocketDisconnect

try:  # the server stack ships websockets, but this module must not require it
    from websockets.exceptions import ConnectionClosed as _WsConnectionClosed
except Exception:  # pragma: no cover - import guard
    _WsConnectionClosed = None

# Starlette and uvicorn raise a bare RuntimeError once the socket is gone; these lower-case
# fragments identify those messages.
_CLOSED_SOCKET_FRAGMENTS = (
    'cannot call "send" once a close message has been sent',
    'cannot call "receive" once a disconnect message has been received',
    "websocket is not connected",
    'need to call "accept" first',
    "unexpected asgi message",
    "after sending 'websocket.close'",
)

# Our own stop_handler closed the socket before the receive loop noticed: a normal shutdown,
# so the loop ends quietly without pushing a second end-of-stream packet.
_TEARDOWN_RACE_FRAGMENTS = ("websocket is not connected", 'need to call "accept" first')

# Transport exceptions raised once the peer is gone (uvicorn, aiohttp, websockets), matched by
# name so this module does not import every transport.
_CLOSED_SOCKET_EXCEPTION_NAMES = frozenset(
    {"ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK", "ClientDisconnected", "WebSocketDisconnect"}
)


def is_socket_closed_error(exc: BaseException) -> bool:
    """True when ``exc`` means the WebSocket is dead, not merely that one operation failed."""
    if isinstance(exc, WebSocketDisconnect):
        return True
    if _WsConnectionClosed is not None and isinstance(exc, _WsConnectionClosed):
        return True
    if isinstance(exc, ConnectionError):  # BrokenPipeError, ConnectionResetError, ...
        return True
    if type(exc).__name__ in _CLOSED_SOCKET_EXCEPTION_NAMES:
        return True
    if isinstance(exc, RuntimeError):
        text = str(exc).lower()
        return any(fragment in text for fragment in _CLOSED_SOCKET_FRAGMENTS)
    return False


def is_teardown_race(exc: BaseException) -> bool:
    """True when the socket was closed by our own teardown before the receive loop noticed."""
    if not isinstance(exc, RuntimeError):
        return False
    text = str(exc).lower()
    return any(fragment in text for fragment in _TEARDOWN_RACE_FRAGMENTS)


__all__ = ["is_socket_closed_error", "is_teardown_race"]
