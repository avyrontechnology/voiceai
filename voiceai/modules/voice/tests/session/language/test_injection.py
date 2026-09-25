"""Language instruction injection at the seam (spec 0034).

Drives `voiceai.modules.voice.session.language.switcher.inject_language_instruction`
directly with a fake session — no TaskManager.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from voiceai.modules.voice.session.language import switcher


def _session(**overrides: Any) -> SimpleNamespace:
    """Fake session: detector double plus injection knobs."""
    detector = MagicMock()
    detector.dominant_language = "hi"
    base: dict[str, Any] = {
        "language_detector": detector,
        "language_injection_mode": "system_only",
        "language_instruction_template": "Speak in {language}.",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _messages() -> list[dict[str, str]]:
    """A system + user message pair."""
    return [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "Namaste."},
    ]


def test_system_only_injects_first_system_message() -> None:
    """System mode prefixes the first system message and stops."""
    session = _session()
    messages = _messages()

    result = switcher.inject_language_instruction(session, messages)

    assert result is messages
    assert messages[0]["content"].startswith("Speak in Hindi.\n\n")
    assert messages[1] == {"role": "user", "content": "Namaste."}


def test_per_turn_injects_every_user_message() -> None:
    """Per-turn mode prefixes all user messages, leaving system alone."""
    session = _session(language_injection_mode="per_turn")
    messages = _messages()

    switcher.inject_language_instruction(session, messages)

    assert messages[0] == {"role": "system", "content": "Be helpful."}
    assert messages[1]["content"].startswith("Speak in Hindi.\n\n")


def test_injection_off_passes_through_untouched() -> None:
    """No language, mode, or template means the list returns identical."""
    messages = _messages()

    assert switcher.inject_language_instruction(_session(language_injection_mode=None), messages) is messages
    assert switcher.inject_language_instruction(
        _session(language_detector=SimpleNamespace(dominant_language=None)), messages
    ) is messages
    assert switcher.inject_language_instruction(_session(language_instruction_template=None), messages) is messages
    assert messages == [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "Namaste."},
    ]


def test_broken_template_is_swallowed() -> None:
    """A template raising on format never kills the call (legacy swallow)."""
    session = _session(language_instruction_template="Speak in {language} {missing}.")
    messages = _messages()

    result = switcher.inject_language_instruction(session, messages)

    assert result is messages
    assert messages[0]["content"] == "Be helpful."
