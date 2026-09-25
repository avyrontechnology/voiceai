"""Switch-language injection at the seam: pool-gated tool authoring (spec 0031).

Drives `voiceai.modules.voice.session.language.switcher.inject_switch_language_tool`
directly with a fake session — no TaskManager. The legacy
`tests/test_switch_tool_injection.py` keeps passing through the delegator.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from voiceai.modules.voice.adapters.language_runtime import TranscriberPool
from voiceai.modules.voice.session.language import switcher


def _session(**overrides: Any) -> SimpleNamespace:
    """Fake session exposing exactly what the injection body reads and writes."""
    pool = MagicMock(spec=TranscriberPool)
    pool.labels = ["en", "hi", "te"]
    base: dict[str, Any] = {
        "tools": {"transcriber": pool},
        "task_config": {"tools_config": {}},
        "kwargs": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_pool_labels_become_the_tool_enum() -> None:
    """Available pool labels land in the schema enum + description."""
    session = _session()

    switcher.inject_switch_language_tool(session)

    (tool,) = session.kwargs["api_tools"]["tools"]
    assert tool["function"]["parameters"]["properties"]["language"]["enum"] == ["en", "hi", "te"]
    assert "en" in tool["function"]["description"] or "Available" in tool["function"]["parameters"]["properties"]["language"]["description"]
    assert session.kwargs["api_tools"]["tools_params"] == {"switch_language": {}}


def test_custom_description_overrides() -> None:
    """A task-level switch_tool_description replaces the schema text."""
    session = _session(task_config={"tools_config": {"switch_tool_description": "Custom switcher."}})

    switcher.inject_switch_language_tool(session)

    (tool,) = session.kwargs["api_tools"]["tools"]
    assert tool["function"]["description"] == "Custom switcher."


def test_no_pool_means_no_injection() -> None:
    """Without a multilingual pool the kwargs stay untouched."""
    session = _session(tools={"transcriber": object()})

    switcher.inject_switch_language_tool(session)

    assert session.kwargs == {}
