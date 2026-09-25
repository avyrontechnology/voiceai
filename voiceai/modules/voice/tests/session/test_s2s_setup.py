"""S2S setup validation at the seam (spec 0035).

Drives `voiceai.modules.voice.session.s2s_runner.setup_s2s` directly with a
fake session — no TaskManager.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from voiceai.modules.voice.session import s2s_runner


def test_valid_config_parses_and_arms_the_event() -> None:
    """Provider, model, and stream-ready event land on the session."""
    session = SimpleNamespace(s2s_config={"provider": "openai_realtime", "provider_config": {"model": "gpt-realtime-2.1"}})

    s2s_runner.setup_s2s(session)

    assert session.s2s_provider_name == "openai_realtime"
    assert session.s2s_model == "gpt-realtime-2.1"
    assert isinstance(session._s2s_stream_ready, asyncio.Event)


def test_invalid_provider_raises() -> None:
    """Unknown providers fail at setup, never mid-conversation."""
    session = SimpleNamespace(s2s_config={"provider": "nope", "provider_config": {}})

    with pytest.raises(Exception):
        s2s_runner.setup_s2s(session)
