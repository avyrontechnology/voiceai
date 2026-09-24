"""Agent provider validation against the catalog (spec 0022, slice 2).

Service tests pin the wiring (typo fails before any extraction spend, open
namespaces pass, unwired compositions skip); walk tests pin the recursion over
raw dicts (LLM leaves at any depth, S2S models, language hints).
"""

from __future__ import annotations

from typing import Any

import pytest

from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.static_methods import audit_provider_config
from voiceai.modules.agents.tests.test_service import (
    CONVERSATION_TASK,
    FakeDefinitionStore,
    agent_model,
    build_service,
)
from voiceai.modules.catalog.models import CatalogEntry
from voiceai.modules.catalog.static_methods import is_valid_language


def _entry(
    modality: str,
    provider: str,
    model: str,
    models_open: bool = False,
    voices: list[dict[str, str]] | None = None,
) -> CatalogEntry:
    """One catalog row for the stub (natural key built by hand)."""
    from voiceai.modules.catalog.models import CatalogVoice

    return CatalogEntry(
        catalog_id=f"{modality}:{provider}:{model}",
        modality=modality,  # type: ignore[arg-type]  # why: test literals, same discipline as the seed table
        provider=provider,
        model=model,
        languages=["en"],
        models_open=models_open,
        voices=[CatalogVoice(name=voice["name"], language=voice.get("language", "en")) for voice in voices or []],
    )


class _Catalog:
    """CatalogService double over hand-made rows with the real language predicate."""

    def __init__(self, entries: list[CatalogEntry] | None = None) -> None:
        self._entries = entries if entries is not None else [
            _entry("asr", "deepgram", "nova-3", models_open=True),
            _entry("asr", "sarvam", "saaras:v3"),
            _entry("llm", "openai", "gpt-4o"),
            _entry("llm", "groq", "llama-3.3-70b-versatile", models_open=True),
            _entry("s2s", "openai_realtime", "gpt-realtime-2.1"),
            _entry("tts", "sarvam", "bulbul:v2", voices=[{"name": "anushka"}, {"name": "arya"}]),
            _entry("tts", "elevenlabs", "eleven_turbo_v2_5", models_open=True, voices=[]),
        ]

    async def entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    @staticmethod
    def is_valid_language(code: str) -> bool:
        return is_valid_language(code)


def _task_with(**tools: Any) -> dict[str, Any]:
    """A conversation task carrying the given tools_config entries."""
    task = dict(CONVERSATION_TASK)
    task["tools_config"] = dict(tools)
    return task


async def test_valid_config_persists() -> None:
    """Known provider/model/language sails through to persistence."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())

    result = await service.create_agent(
        agent_model(_task_with(transcriber={"provider": "deepgram", "model": "nova-3", "language": "en"})), None
    )

    assert result["state"] == "created"


async def test_typo_model_fails_with_valid_values() -> None:
    """A typo'd model in a closed namespace fails fast with the valid values."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())
    model = agent_model(_task_with(transcriber={"provider": "sarvam", "model": "saaras:v9", "language": "en"}))

    with pytest.raises(AgentConfigInvalidError, match="saaras:v9"):
        await service.create_agent(model, None)


async def test_open_namespace_accepts_new_models() -> None:
    """Vendor-extended namespaces never false-reject (suggestions, not gates)."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())
    model = agent_model(_task_with(transcriber={"provider": "deepgram", "model": "nova-4-future"}))

    result = await service.create_agent(model, None)

    assert result["state"] == "created"


async def test_unwired_catalog_skips_with_warning() -> None:
    """Compositions without a catalog keep working (container always wires it)."""
    service = build_service(definitions=FakeDefinitionStore())
    model = agent_model(_task_with(transcriber={"provider": "deepgram", "model": "nova-3"}))

    result = await service.create_agent(model, None)

    assert result["state"] == "created"


def _raw_task_with(**tools: Any) -> dict[str, Any]:
    """A task dict without schema normalization (what the audit script reads)."""
    return {"tools_config": dict(tools)}


def _raw_config(*tasks: dict[str, Any]) -> dict[str, Any]:
    """A stored-config-shaped mapping around raw tasks."""
    return {"tasks": list(tasks)}


def _rows() -> list[dict[str, Any]]:
    """Dumped stub rows for the pure walk tests."""
    return [entry.model_dump() for entry in _Catalog()._entries]


def test_walk_accepts_a_clean_config() -> None:
    """No problems for resolving providers, models, and languages."""
    config = _raw_config(
        _raw_task_with(
            transcriber={"provider": "deepgram", "model": "nova-3", "language": "en", "language_hints": ["hi"]},
            llm_agent={"provider": "openai", "model": "gpt-4o"},
            s2s={"provider": "openai_realtime", "provider_config": {"model": "gpt-realtime-2.1"}},
        )
    )

    assert audit_provider_config(config, _rows(), is_valid_language) == []


def test_walk_reports_unknown_llm_provider_and_bad_language() -> None:
    """Free-string LLM providers fail here; malformed codes fail with paths."""
    config = _raw_config(
        _raw_task_with(
            transcriber={"provider": "deepgram", "model": "nova-3", "language": "english"},
            llm_agent={"nested": {"provider": "open ia", "model": "gpt-4o"}},
        )
    )

    problems = audit_provider_config(config, _rows(), is_valid_language)

    assert any("open ia" in problem and "openai" in problem for problem in problems)
    assert any("english" in problem and "transcriber" in problem for problem in problems)


def test_walk_reports_closed_model_mismatch() -> None:
    """Closed namespaces must match exactly (open ones accept anything)."""
    config = _raw_config(
        _raw_task_with(s2s={"provider": "openai_realtime", "provider_config": {"model": "nope"}})
    )

    problems = audit_provider_config(config, _rows(), is_valid_language)

    assert len(problems) == 1
    assert "gpt-realtime-2.1" in problems[0]


def test_walk_accepts_a_curated_synthesizer_row() -> None:
    """Model, voice, and language resolve against the matched row."""
    config = _raw_config(
        _raw_task_with(
            synthesizer={
                "provider": "sarvam",
                "provider_config": {"model": "bulbul:v2", "voice": "anushka", "language": "hi"},
            }
        )
    )

    assert audit_provider_config(config, _rows(), is_valid_language) == []


def test_walk_reports_foreign_voice_and_bad_synth_language() -> None:
    """Voices outside the curated set fail with names; bad codes fail with paths."""
    config = _raw_config(
        _raw_task_with(
            synthesizer={
                "provider": "sarvam",
                "provider_config": {"model": "bulbul:v2", "voice": "morgan", "language": "xx_YY"},
            }
        )
    )

    problems = audit_provider_config(config, _rows(), is_valid_language)

    assert any("morgan" in problem and "anushka" in problem for problem in problems)
    assert any("xx_YY" in problem and "synthesizer" in problem for problem in problems)


def test_walk_skips_voice_check_without_curated_voices() -> None:
    """Rows without a curated voice set never false-reject (gradual rule)."""
    config = _raw_config(
        _raw_task_with(
            synthesizer={
                "provider": "elevenlabs",
                "provider_config": {"model": "eleven_turbo_v2_5", "voice": "anything-goes"},
            }
        )
    )

    assert audit_provider_config(config, _rows(), is_valid_language) == []
