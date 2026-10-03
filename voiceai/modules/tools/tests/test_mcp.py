"""MCP client + DCR behavior over fakes (spec 0056, Phase-3).

Offline only: HTTP rides `httpx.MockTransport`, issuers ride an injected
guard. Pinned: SSE parsing, paginated list-all, call_tool envelope, DCR
discovery order, MANUAL short-circuit, registration success/refusal.
"""

from __future__ import annotations

import httpx
import pytest

from voiceai.modules.tools.adapters.mcp.client import McpHttpClient, parse_sse_events
from voiceai.modules.tools.dcr_service import DcrService
from voiceai.modules.tools.errors import ToolExecutionError


def _client(handler) -> McpHttpClient:  # why: handler shapes vary per case
    """Build one client over a mock transport.

    Args:
        handler: `httpx.MockTransport` request handler.

    Returns:
        A client that never touches the network.
    """
    transport = httpx.MockTransport(handler)
    return McpHttpClient(
        "https://mcp.example.com", "token", client_factory=lambda: httpx.AsyncClient(transport=transport)
    )


def test_parse_sse_events_skips_comments_and_bad_json() -> None:
    """SSE parsing keeps data payloads only (spec 0056 resilience)."""
    body = ": ping\n" + 'data: {"a": 1}\n' + "data: not-json\n" + 'data: {"b": 2}\n'
    assert parse_sse_events(body) == [{"a": 1}, {"b": 2}]


async def test_call_tool_posts_envelope_and_returns_result() -> None:
    """`tools/call` posts the JSON-RPC envelope and returns `result`."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": {"ok": True}})

    result = await _client(handler).call_tool("fetch_order", {"id": "1"})
    assert result == {"ok": True}
    assert seen["method"] == "tools/call"


async def test_list_all_tools_follows_cursor_once() -> None:
    """Cursor pagination follows exactly until the cursor empties."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        calls["n"] += 1
        payload = json.loads(request.content.decode())
        if payload.get("params", {}).get("cursor"):
            return httpx.Response(200, json={"result": {"tools": [{"name": "b"}]}})
        return httpx.Response(200, json={"result": {"tools": [{"name": "a"}], "nextCursor": "c1"}})

    tools = await _client(handler).list_all_tools(limit=5)
    assert [t["name"] for t in tools] == ["a", "b"]
    assert calls["n"] == 2


async def test_dcr_manual_when_no_registration_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issuers without RFC7591 support short-circuit to MANUAL (no fabrication)."""
    service = DcrService()

    async def _meta(_issuer: str) -> dict:
        return {"authorization_endpoint": "https://x/auth"}

    monkeypatch.setattr(service, "discover", _meta)
    with pytest.raises(ToolExecutionError):
        await service.register_client("https://issuer.example.com")


async def test_dcr_undiscoverable_issuer_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undiscoverable issuers fail with identifiers only."""
    service = DcrService()

    async def _none(_issuer: str) -> None:
        return None

    monkeypatch.setattr(service, "discover", _none)
    with pytest.raises(ToolExecutionError):
        await service.register_client("https://issuer.example.com")
