"""Characterization: `WebhookAgent` (spec 0002, step A6).

Pins the legacy surface before the move with a fully faked `aiohttp` module — the module
attribute is resolved through `WebhookAgent.__module__`, so the same patch target stays
live after the class moves to `voiceai.modules.agents.brains.webhook`.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from voiceai.agent_types.webhook_agent import WebhookAgent

WEBHOOK_URL = "https://hooks.example.test/receive"
PAYLOAD = {"call_id": "abc", "status": "done"}


class FakeResponse:
    """An async-context-manager response with a status and a text body."""

    def __init__(self, status=200, body="ok"):
        self.status = status
        self.body = body

    async def text(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    """An async-context-manager session recording `post` calls."""

    def __init__(self, response, error=None):
        self.response = response
        self.error = error
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, json=None):
        if self.error is not None:
            raise self.error
        self.posts.append((url, json))
        return self.response


@pytest.fixture
def fake_session(monkeypatch):
    """Install a fake `aiohttp` module on the class's live module; return the session."""
    session = FakeSession(FakeResponse())

    def install(response=None, error=None):
        if response is not None:
            session.response = response
        session.error = error
        module = importlib.import_module(WebhookAgent.__module__)
        monkeypatch.setattr(module, "aiohttp", SimpleNamespace(ClientSession=lambda: session))
        return session

    return install


def test_default_payload_is_an_empty_dict():
    assert WebhookAgent(WEBHOOK_URL).payload == {}
    assert WebhookAgent(WEBHOOK_URL, {"a": 1}).payload == {"a": 1}


async def test_execute_without_a_url_answers_none():
    """A falsy webhook URL short-circuits before any HTTP object is touched."""
    assert await WebhookAgent("").execute(PAYLOAD) is None
    assert await WebhookAgent(None).execute(PAYLOAD) is None


async def test_execute_posts_the_payload_and_answers_true_on_200(fake_session):
    session = fake_session(response=FakeResponse(status=200))

    assert await WebhookAgent(WEBHOOK_URL).execute(PAYLOAD) is True
    assert session.posts == [(WEBHOOK_URL, PAYLOAD)]


async def test_execute_answers_none_on_non_200(fake_session):
    fake_session(response=FakeResponse(status=500, body="server error"))

    assert await WebhookAgent(WEBHOOK_URL).execute(PAYLOAD) is None


async def test_execute_with_a_null_payload_skips_the_post(fake_session):
    """`payload=None` opens the session but never posts, answering None (legacy parity)."""
    session = fake_session()

    assert await WebhookAgent(WEBHOOK_URL).execute(None) is None
    assert session.posts == []


async def test_execute_swallows_session_errors_to_none(fake_session):
    fake_session(error=RuntimeError("connection refused"))

    assert await WebhookAgent(WEBHOOK_URL).execute(PAYLOAD) is None
