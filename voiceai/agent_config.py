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


_ALLOWED_API_METHODS = frozenset({"GET", "POST"})
_ALLOWED_URL_SCHEMES = ("http", "https")


def _check_api_tools(cfg: Any, path: str, issues: List[ConfigIssue]) -> None:
    """Validate api_tools URLs and param templates with precise paths.

    Checks scheme/host presence (SSRF DNS validation runs at call time in trigger_api),
    HTTP method allowlist, and that legacy %(field)s templates are well-formed JSON templates.
    $var markers are the preferred type-safe form and always pass template checks.
    """
    if cfg is None:
        return
    if not isinstance(cfg, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    params = cfg.get("tools_params")
    if params is None:
        return
    if not isinstance(params, Mapping):
        issues.append(ConfigIssue(f"{path}.tools_params", "must be an object of tool-name -> API params"))
        return
    for tool_name, entry in params.items():
        entry_path = f"{path}.tools_params.{tool_name}"
        if not isinstance(entry, Mapping):
            issues.append(ConfigIssue(entry_path, "must be an object"))
            continue
        url = entry.get("url")
        if url is not None:
            if not isinstance(url, str) or not url.strip():
                issues.append(ConfigIssue(f"{entry_path}.url", "must be a non-empty URL string"))
            else:
                from urllib.parse import urlsplit as _split

                try:
                    parsed = _split(url.strip())
                except Exception:
                    issues.append(ConfigIssue(f"{entry_path}.url", "must be a valid URL"))
                    continue
                if (parsed.scheme or "").lower() not in _ALLOWED_URL_SCHEMES:
                    issues.append(
                        ConfigIssue(
                            f"{entry_path}.url", f"unsupported scheme {parsed.scheme!r}; only http/https allowed"
                        )
                    )
                elif not parsed.hostname:
                    issues.append(ConfigIssue(f"{entry_path}.url", "URL must include a host"))
                elif parsed.hostname.lower() in ("169.254.169.254", "127.0.0.1", "0.0.0.0"):
                    issues.append(ConfigIssue(f"{entry_path}.url", "URL targets a non-public address"))
        method = entry.get("method")
        if method is not None:
            if not isinstance(method, str) or method.upper() not in _ALLOWED_API_METHODS:
                issues.append(
                    ConfigIssue(f"{entry_path}.method", f"unsupported method {method!r}; only GET/POST are supported")
                )
        param = entry.get("param")
        if isinstance(param, str) and "%(" in param:
            # Legacy template: must contain a closing )s and render to JSON with dummy values.
            if ")s" not in param:
                issues.append(ConfigIssue(f"{entry_path}.param", "legacy %(field)s template is malformed"))
        pre_url = entry.get("pre_call_webhook_url")
        if pre_url is not None and isinstance(pre_url, str) and pre_url.strip():
            from urllib.parse import urlsplit as _split2

            try:
                p2 = _split2(pre_url.strip())
                if (p2.scheme or "").lower() not in _ALLOWED_URL_SCHEMES or not p2.hostname:
                    issues.append(ConfigIssue(f"{entry_path}.pre_call_webhook_url", "must be a valid http(s) URL"))
            except Exception:
                issues.append(ConfigIssue(f"{entry_path}.pre_call_webhook_url", "must be a valid http(s) URL"))


def _check_task_config(cfg: Any, path: str, issues: List[ConfigIssue]) -> None:
    if cfg is None:
        return
    if not isinstance(cfg, Mapping):
        issues.append(ConfigIssue(path, "must be an object"))
        return
    hangup = cfg.get("hangup_after_silence")
    if hangup is not None and not isinstance(hangup, (int, float)):
        issues.append(ConfigIssue(f"{path}.hangup_after_silence", "must be a number of seconds"))
    terminate = cfg.get("call_terminate")
    if terminate is not None and not isinstance(terminate, (int, float)):
        issues.append(ConfigIssue(f"{path}.call_terminate", "must be a number of seconds"))


def _check_rag_block(llm_agent: Any, path: str, issues: List[ConfigIssue]) -> None:
    """Validate rag_config vector-store identifiers wherever they appear (global or per-node)."""
    if not isinstance(llm_agent, Mapping):
        return
    llm_config = llm_agent.get("llm_config")
    if not isinstance(llm_config, Mapping):
        return
    for scope_path, rag in ((f"{path}.llm_config.rag_config", llm_config.get("rag_config")),):
        if rag is None:
            continue
        if not isinstance(rag, Mapping):
            issues.append(ConfigIssue(scope_path, "must be an object"))
            continue
        store = rag.get("vector_store")
        if store is None:
            continue
        if not isinstance(store, Mapping):
            issues.append(ConfigIssue(f"{scope_path}.vector_store", "must be an object"))
            continue
        provider = store.get("provider")
        if provider is None:
            issues.append(ConfigIssue(f"{scope_path}.vector_store.provider", "provider is required"))
        cfg = store.get("provider_config")
        if cfg is not None and not isinstance(cfg, Mapping):
            issues.append(ConfigIssue(f"{scope_path}.vector_store.provider_config", "must be an object"))
    nodes = llm_config.get("nodes")
    if isinstance(nodes, list):
        for idx, node in enumerate(nodes):
            if not isinstance(node, Mapping):
                continue
            rag = node.get("rag_config")
            if rag is None:
                continue
            npath = f"{path}.llm_config.nodes[{idx}].rag_config"
            if not isinstance(rag, Mapping):
                issues.append(ConfigIssue(npath, "must be an object"))
                continue
            store = rag.get("vector_store")
            if store is not None and not isinstance(store, Mapping):
                issues.append(ConfigIssue(f"{npath}.vector_store", "must be an object"))


def _check_welcome(raw: Mapping[str, Any], issues: List[ConfigIssue]) -> None:
    msg = raw.get("agent_welcome_message")
    if msg is None:
        return
    if not isinstance(msg, (str, dict)):
        issues.append(ConfigIssue("agent_welcome_message", "must be a string or per-language map"))
        return
    if isinstance(msg, dict):
        for lang, text in msg.items():
            if not isinstance(text, str) or not text.strip():
                issues.append(ConfigIssue(f"agent_welcome_message.{lang}", "must be a non-empty string"))


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
    _check_api_tools(tools.get("api_tools"), f"{path}.tools_config.api_tools", issues)
    _check_task_config(task.get("task_config"), f"{path}.task_config", issues)
    _check_rag_block(tools.get("llm_agent"), f"{path}.tools_config.llm_agent", issues)


def semantic_issues(raw: Mapping[str, Any]) -> List[ConfigIssue]:
    """Every engine-level problem in ``raw``; empty when the engine can build a call from it."""
    issues: List[ConfigIssue] = []
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        issues.append(ConfigIssue("tasks", "at least one task is required"))
        return issues
    _check_welcome(raw, issues)
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
    from voiceai.core.environment import get_str

    return (get_str(STRICT_ENV, "") or "").strip().lower() in ("1", "true", "yes")


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
