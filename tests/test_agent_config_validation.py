"""Guards validate_agent_config: bad configs fail with a path, good ones (incl. pools) pass."""

import copy

import pytest

from voiceai.agent_config import semantic_issues, validate_agent_config
from voiceai.errors import ConfigurationError, ErrorCode

GOOD = {
    "agent_name": "Support",
    "agent_type": "other",
    "agent_welcome_message": "Hello",
    "tasks": [
        {
            "task_type": "conversation",
            "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm", "synthesizer"]]},
            "tools_config": {
                "input": {"provider": "twilio", "format": "wav"},
                "output": {"provider": "twilio", "format": "wav"},
                "transcriber": {"provider": "deepgram", "model": "nova-2", "stream": True, "language": "en"},
                "llm_agent": {
                    "agent_type": "simple_llm_agent",
                    "agent_flow_type": "streaming",
                    "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
                },
                "synthesizer": {
                    "provider": "elevenlabs",
                    "provider_config": {"voice": "George", "voice_id": "abc", "model": "eleven_turbo_v2_5"},
                    "stream": True,
                    "audio_format": "wav",
                },
            },
            "task_config": {"hangup_after_silence": 30},
        }
    ],
}


def _bad(mutate):
    config = copy.deepcopy(GOOD)
    mutate(config)
    return config


def _paths(exc):
    return [issue["path"] for issue in exc.details["issues"]]


def test_good_config_passes_and_is_returned_unchanged():
    result = validate_agent_config(copy.deepcopy(GOOD))
    assert result == GOOD


def test_unknown_transcriber_provider_has_a_path():
    def mutate(c):
        c["tasks"][0]["tools_config"]["transcriber"]["provider"] = "deepgramm"

    with pytest.raises(ConfigurationError) as info:
        validate_agent_config(_bad(mutate), structural=False)
    assert info.value.code is ErrorCode.CONFIGURATION_INVALID
    assert info.value.http_status == 400
    assert info.value.path == "tasks[0].tools_config.transcriber.provider"
    assert "deepgramm" in info.value.message


def test_pipeline_referencing_unconfigured_tool_is_reported():
    def mutate(c):
        c["tasks"][0]["tools_config"]["synthesizer"] = None

    with pytest.raises(ConfigurationError) as info:
        validate_agent_config(_bad(mutate), structural=False)
    assert "tasks[0].tools_config.synthesizer" in _paths(info.value)


def test_every_issue_is_listed_not_just_the_first():
    def mutate(c):
        c["tasks"][0]["tools_config"]["synthesizer"]["provider"] = "nope"
        c["tasks"][0]["tools_config"]["llm_agent"]["llm_config"]["provider"] = "nope-llm"
        c["tasks"][0]["tools_config"]["output"]["provider"] = "carrier-pigeon"

    issues = semantic_issues(_bad(mutate))
    paths = {i.path for i in issues}
    assert {
        "tasks[0].tools_config.synthesizer.provider",
        "tasks[0].tools_config.llm_agent.llm_config.provider",
        "tasks[0].tools_config.output.provider",
    } <= paths


def test_unknown_task_type_and_pipeline_tool():
    def mutate(c):
        c["tasks"][0]["task_type"] = "banter"
        c["tasks"][0]["toolchain"]["pipelines"] = [["transcriber", "magic"]]

    issues = {i.path: i.message for i in semantic_issues(_bad(mutate))}
    assert "banter" in issues["tasks[0].task_type"]
    assert "magic" in issues["tasks[0].toolchain.pipelines[0]"]


def test_multilingual_pools_validate_every_entry_and_the_active_label():
    def mutate(c):
        tools = c["tasks"][0]["tools_config"]
        tools["transcriber"] = {
            "provider": "deepgram",
            "active": "fr",
            "multilingual": {"en": {"provider": "deepgram"}, "hi": {"provider": "not-a-provider"}},
        }
        tools["synthesizer"] = {
            "provider": "elevenlabs",
            "provider_config": {"voice": "x", "voice_id": "y"},
            "active": "en",
            "multilingual": {
                "en": {"provider": "elevenlabs", "provider_config": {"voice": "x"}},
                "hi": {"provider": "kalpa"},
            },
        }

    issues = {i.path for i in semantic_issues(_bad(mutate))}
    assert "tasks[0].tools_config.transcriber.active" in issues
    assert "tasks[0].tools_config.transcriber.multilingual.hi.provider" in issues
    assert "tasks[0].tools_config.synthesizer.multilingual.hi.provider_config" in issues


def test_structural_errors_raise_by_default_but_only_warn_in_lenient_mode(monkeypatch, caplog):
    monkeypatch.delenv("AGENT_CONFIG_STRICT", raising=False)
    config = _bad(lambda c: c.pop("agent_name"))
    with pytest.raises(ConfigurationError) as info:
        validate_agent_config(config)
    assert info.value.path.startswith("agent_config")
    assert validate_agent_config(config, structural=False) == config  # engine-level checks still pass
    assert any("does not match the current schema" in r.message for r in caplog.records)


def test_strict_env_makes_lenient_mode_raise(monkeypatch):
    monkeypatch.setenv("AGENT_CONFIG_STRICT", "1")
    with pytest.raises(ConfigurationError):
        validate_agent_config(_bad(lambda c: c.pop("agent_name")), structural=False)


def test_non_object_and_missing_tasks():
    with pytest.raises(ConfigurationError) as info:
        validate_agent_config(["not", "a", "dict"])
    assert info.value.path == "agent_config"
    with pytest.raises(ConfigurationError) as info:
        validate_agent_config({"agent_name": "x", "tasks": []}, structural=False)
    assert info.value.path == "tasks"
