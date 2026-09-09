"""Validate an agent configuration at the boundary, before a call is built from it.

Historically a bad key was discovered deep inside ``TaskManager.__init__`` as a ``KeyError``
or ``'NoneType' is not callable`` and the caller saw a generic 500 or a silently dropped
socket. ``validate_agent_config`` runs two layers of checks and raises one
``ConfigurationError`` whose ``details["issues"]`` lists every problem with a path such as
``tasks[0].tools_config.synthesizer.provider``:

* structural — the pydantic ``AgentModel`` (optional: stored configs written by older
  versions can be looser than today's model, so the websocket path runs this layer in
  report-only mode unless ``AGENT_CONFIG_STRICT=1``);
* semantic — the things the engine will actually index: known task types, pipelines that
  reference configured tools, provider names present in the registries (including every
  entry of a multilingual pool), and the required provider sub-keys.

The raw mapping is returned unchanged so the engine keeps consuming exactly what was stored.
"""

from __future__ import annotations

import copy
import os
from dataclasses import asdict, dataclass
from typing import Any, Iterable, List, Mapping, Optional

from pydantic import ValidationError as PydanticValidationError

from voiceai.errors import ConfigurationError
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

KNOWN_TASK_TYPES = frozenset({"conversation", "extraction", "summarization", "webhook"})
PIPELINE_TOOLS = frozenset({"transcriber", "llm", "synthesizer", "s2s"})
KNOWN_AGENT_TYPES = frozenset(
    {"simple_llm_agent", "graph_agent", "knowledgebase_agent", "multiagent", "llm_agent_graph"}
)
STRICT_ENV = "AGENT_CONFIG_STRICT"


@dataclass(frozen=True)
class ConfigIssue:
    path: str
    message: str


def _registries() -> dict:
    # Imported lazily: voiceai.providers pulls in every provider SDK.
    from voiceai import providers

    return {
        "input": providers.SUPPORTED_INPUT_HANDLERS,
        "output": providers.SUPPORTED_OUTPUT_HANDLERS,
        "transcriber": providers.SUPPORTED_TRANSCRIBER_PROVIDERS,
        "transcriber_models": providers.SUPPORTED_TRANSCRIBER_MODELS,
        "synthesizer": providers.SUPPORTED_SYNTHESIZER_MODELS,
        "llm": providers.SUPPORTED_LLM_PROVIDERS,
        "s2s": providers.SUPPORTED_S2S_PROVIDERS,
    }


def _known(name: Any, registry: Mapping[str, Any]) -> bool:
    return isinstance(name, str) and name in registry


def _check_transcriber(cfg: Any, path: str, reg: dict, issues: List[ConfigIssue]) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    multilingual = cfg.get("multilingual")
    if multilingual:
        if not isinstance(multilingual, Mapping) or not multilingual:
            issues.append(
                ConfigIssue(f"{path}.multilingual", "must be a non-empty object of label -> transcriber config")
            )
            return
        active = cfg.get("active")
        if active is not None and active not in multilingual:
            issues.append(
                ConfigIssue(
                    f"{path}.active", f"'{active}' is not one of the multilingual labels {sorted(multilingual)}"
                )
            )
        for label, entry in multilingual.items():
            entry_path = f"{path}.multilingual.{label}"
            if not isinstance(entry, Mapping):
                issues.append(ConfigIssue(entry_path, "must be an object"))
                continue
            provider = entry.get("provider")
            if provider is not None:
                if not _known(provider, reg["transcriber"]):
                    issues.append(ConfigIssue(f"{entry_path}.provider", f"unknown transcriber provider '{provider}'"))
            elif not _known(entry.get("model"), reg["transcriber_models"]):
                issues.append(ConfigIssue(f"{entry_path}.provider", "provider is required"))
        return
    provider = cfg.get("provider")
    if provider is not None:
        if not _known(provider, reg["transcriber"]):
            issues.append(ConfigIssue(f"{path}.provider", f"unknown transcriber provider '{provider}'"))
    elif not _known(cfg.get("model"), reg["transcriber_models"]):
        issues.append(ConfigIssue(f"{path}.provider", "provider is required"))


def _as_mapping(value: Any) -> Any:
    """Config blocks may arrive as pydantic models (validators build them); read them as dicts."""
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value


def _check_synth_entry(entry: Any, path: str, reg: dict, issues: List[ConfigIssue], *, require_voice: bool) -> None:
    if not isinstance(entry, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    provider = entry.get("provider")
    if not _known(provider, reg["synthesizer"]):
        issues.append(
            ConfigIssue(
                f"{path}.provider", f"unknown synthesizer provider '{provider}'" if provider else "provider is required"
            )
        )
    provider_config = _as_mapping(entry.get("provider_config"))
    if not isinstance(provider_config, Mapping):
        issues.append(ConfigIssue(f"{path}.provider_config", "provider_config object is required"))
    elif require_voice and not provider_config.get("voice"):
        issues.append(ConfigIssue(f"{path}.provider_config.voice", "voice is required"))


def _check_synthesizer(cfg: Any, path: str, reg: dict, issues: List[ConfigIssue]) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    multilingual = cfg.get("multilingual")
    if multilingual is not None:
        if not isinstance(multilingual, Mapping) or not multilingual:
            issues.append(
                ConfigIssue(f"{path}.multilingual", "must be a non-empty object of label -> synthesizer config")
            )
            return
        active = cfg.get("active")
        if active is not None and active not in multilingual:
            issues.append(
                ConfigIssue(
                    f"{path}.active", f"'{active}' is not one of the multilingual labels {sorted(multilingual)}"
                )
            )
        for label, entry in multilingual.items():
            _check_synth_entry(entry, f"{path}.multilingual.{label}", reg, issues, require_voice=False)
        return
    _check_synth_entry(cfg, path, reg, issues, require_voice=True)


def _check_llm_agent(cfg: Any, path: str, reg: dict, issues: List[ConfigIssue]) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    agent_type = cfg.get("agent_type")
    if agent_type is not None and agent_type not in KNOWN_AGENT_TYPES:
        issues.append(
            ConfigIssue(
                f"{path}.agent_type", f"unknown agent_type '{agent_type}'; expected one of {sorted(KNOWN_AGENT_TYPES)}"
            )
        )
    llm_config = cfg.get("llm_config")
    provider = llm_config.get("provider") if isinstance(llm_config, Mapping) else cfg.get("provider")
    if provider is not None and not _known(provider, reg["llm"]):
        where = f"{path}.llm_config.provider" if isinstance(llm_config, Mapping) else f"{path}.provider"
        issues.append(ConfigIssue(where, f"unknown LLM provider '{provider}'"))


def _check_task(task: Any, path: str, reg: dict, issues: List[ConfigIssue]) -> None:
    if not isinstance(task, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    task_type = task.get("task_type", "conversation")
    if task_type not in KNOWN_TASK_TYPES:
        issues.append(
            ConfigIssue(
                f"{path}.task_type", f"unknown task_type '{task_type}'; expected one of {sorted(KNOWN_TASK_TYPES)}"
            )
        )

    tools = task.get("tools_config")
    if not isinstance(tools, Mapping):
        issues.append(ConfigIssue(f"{path}.tools_config", "tools_config object is required"))
        tools = {}

    toolchain = task.get("toolchain")
    pipelines = toolchain.get("pipelines") if isinstance(toolchain, Mapping) else None
    if not isinstance(pipelines, list) or not pipelines:
        issues.append(ConfigIssue(f"{path}.toolchain.pipelines", "at least one pipeline is required"))
        pipelines = []
    configured = {
        "transcriber": tools.get("transcriber") is not None,
        "synthesizer": tools.get("synthesizer") is not None,
        "llm": tools.get("llm_agent") is not None,
        "s2s": tools.get("s2s") is not None,
    }
    for i, pipeline in enumerate(pipelines):
        p_path = f"{path}.toolchain.pipelines[{i}]"
        if not isinstance(pipeline, list) or not pipeline:
            issues.append(ConfigIssue(p_path, "must be a non-empty list of tool names"))
            continue
        for tool in pipeline:
            if tool not in PIPELINE_TOOLS:
                issues.append(
                    ConfigIssue(p_path, f"unknown pipeline tool '{tool}'; expected one of {sorted(PIPELINE_TOOLS)}")
                )
            elif not configured[tool]:
                key = "llm_agent" if tool == "llm" else tool
                issues.append(
                    ConfigIssue(f"{path}.tools_config.{key}", f"pipeline references '{tool}' but it is not configured")
                )

    for direction in ("input", "output"):
        io_cfg = tools.get(direction)
        if io_cfg is None:
            continue
        if not isinstance(io_cfg, Mapping):
            issues.append(ConfigIssue(f"{path}.tools_config.{direction}", "must be an object"))
        elif not _known(io_cfg.get("provider"), reg[direction]):
            issues.append(
                ConfigIssue(
                    f"{path}.tools_config.{direction}.provider",
                    f"unknown {direction} provider '{io_cfg.get('provider')}'",
                )
            )

    _check_transcriber(tools.get("transcriber"), f"{path}.tools_config.transcriber", reg, issues)
    _check_synthesizer(tools.get("synthesizer"), f"{path}.tools_config.synthesizer", reg, issues)
    _check_llm_agent(tools.get("llm_agent"), f"{path}.tools_config.llm_agent", reg, issues)
    s2s = tools.get("s2s")
    if s2s is not None:
        if not isinstance(s2s, Mapping):
            issues.append(ConfigIssue(f"{path}.tools_config.s2s", "must be an object"))
        elif not _known(s2s.get("provider"), reg["s2s"]):
            issues.append(
                ConfigIssue(f"{path}.tools_config.s2s.provider", f"unknown s2s provider '{s2s.get('provider')}'")
            )


def semantic_issues(raw: Mapping[str, Any]) -> List[ConfigIssue]:
    """Every engine-level problem in ``raw``; empty when the engine can build a call from it."""
    issues: List[ConfigIssue] = []
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        issues.append(ConfigIssue("tasks", "at least one task is required"))
        return issues
    reg = _registries()
    for index, task in enumerate(tasks):
        _check_task(task, f"tasks[{index}]", reg, issues)
    return issues


def _raise(issues: Iterable[ConfigIssue], *, prefix: str) -> None:
    items = list(issues)
    first = items[0]
    summary = f"{first.path}: {first.message}"
    if len(items) > 1:
        summary += f" (+{len(items) - 1} more)"
    raise ConfigurationError(summary, path=first.path, issues=[asdict(i) for i in items])


def structural_strict() -> bool:
    return os.getenv(STRICT_ENV, "").strip().lower() in ("1", "true", "yes")


def validate_agent_config(raw: Any, *, structural: bool = True, name: Optional[str] = None) -> dict:
    """Return ``raw`` (a plain dict) if the engine can run it; raise ``ConfigurationError`` otherwise.

    ``structural=False`` demotes pydantic model failures to a warning log; semantic issues
    always raise because they are exactly the keys the engine would crash on.
    """
    label = name or (raw.get("agent_name") if isinstance(raw, Mapping) else None) or "agent"
    if not isinstance(raw, Mapping):
        raise ConfigurationError("agent config must be a JSON object", path="agent_config")

    from voiceai.models import AgentModel  # local: voiceai.models is heavy and imports provider enums

    try:
        # Validate a copy: some "before" validators historically wrote model instances back
        # into the dict they were given, and the engine must keep the stored plain-JSON shape.
        AgentModel(**copy.deepcopy(dict(raw)))
    except PydanticValidationError as exc:
        err = ConfigurationError.from_validation_error(exc, prefix="agent_config")
        if structural or structural_strict():
            raise err
        logger.warning(
            "agent %s: config does not match the current schema (%s); continuing with engine checks", label, err.message
        )
    except Exception as exc:  # a broken validator must not turn into a 500
        err = ConfigurationError(f"agent config could not be validated: {exc}", path="agent_config", cause=exc)
        if structural or structural_strict():
            raise err
        logger.warning("agent %s: %s", label, err.message)

    issues = semantic_issues(raw)
    if issues:
        _raise(issues, prefix="agent_config")
    return dict(raw)


__all__ = [
    "ConfigIssue",
    "KNOWN_TASK_TYPES",
    "PIPELINE_TOOLS",
    "KNOWN_AGENT_TYPES",
    "semantic_issues",
    "validate_agent_config",
]
