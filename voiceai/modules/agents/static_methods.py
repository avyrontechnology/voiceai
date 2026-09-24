"""Pure agent-record and prompt-selection functions (AGENTS.md rule 1g; spec 0002, A3).

The record helpers moved verbatim from ``voiceai/platform/agent_records.py`` lines 19-45
(now a ``# legacy-shim(spec-0002)`` re-export of this module); only the logger changed, to
the single ``otobaai`` channel (rule 3). Agent configs live under bare UUID keys while
platform data uses ``:``-namespaced keys (plus index sets). The legacy ``/all`` endpoint
scans ``KEYS *``, so without filtering it would return platform records as fake agents —
and a positional zip of keys to values misaligns IDs as soon as any key is skipped. These
helpers keep that filtering testable without importing the engine.

The prompt-selection helpers mirror the shape the engine reads back from the stored
conversation-details payload (``task_manager.py`` lines 2149-2162): 1-based ``task_<n>``
keys, and for multiagent configs the nested ``task_1.{agent_name}.system_prompt`` block.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from voiceai.common.logger import get_logger
from voiceai.modules.agents.constants import (
    AGENT_DATA_KEY,
    AGENT_ID_KEY,
    MODULE_NAME,
    REDIS_KEY_NAMESPACE_SEPARATOR,
    TASKS_KEY,
)

__all__ = [
    "audit_provider_config",
    "collect_agent_records",
    "is_agent_key",
    "parse_agent_record",
    "select_multiagent_system_prompt",
    "select_task_prompts",
    "task_prompt_key",
]

_LOGGER = get_logger(MODULE_NAME)

_LOG_UNREADABLE_RECORD: Final[str] = "Skipping unreadable agent record %s: %s"

# The engine keys per-task prompt blocks 1-based: task_manager.py:2149 builds
# `"task_{}".format(task_id + 1)`; this template is that contract, named.
_TASK_PROMPT_KEY_TEMPLATE: Final[str] = "task_{number}"
_TASK_INDEX_OFFSET: Final[int] = 1
# Inside a multiagent task block every agent's prompt lives under this key
# (task_manager.py:2157 reads `prompts[agent]["system_prompt"]`).
_SYSTEM_PROMPT_KEY: Final[str] = "system_prompt"


def is_agent_key(key: str) -> bool:
    """Report whether a redis key names an agent record.

    Bare UUID agent keys never contain a colon; namespaced platform data always does
    (the bare-UUID key contract preserved by spec 0002).

    Args:
        key: The redis key to classify.

    Returns:
        ``True`` only for bare (un-namespaced) keys.
    """
    return REDIS_KEY_NAMESPACE_SEPARATOR not in key


def parse_agent_record(key: str, raw: str | None) -> dict[str, Any] | None:
    """Build one ``/all`` record from a key and its raw redis value, or reject it.

    A genuine agent record is a JSON object on a bare key whose ``tasks`` value is a
    list; anything else — namespaced keys, empty values, unparseable JSON, non-dict
    payloads — answers ``None`` so the directory scan silently skips it, exactly as the
    legacy endpoint does.

    Args:
        key: The redis key the value was read from.
        raw: The raw string value, or ``None`` when the read produced nothing.

    Returns:
        ``{"agent_id": key, "data": <parsed config>}`` for genuine records, else ``None``.
    """
    if not is_agent_key(key) or not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        _LOGGER.warning(_LOG_UNREADABLE_RECORD, key, exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get(TASKS_KEY), list):
        return None
    return {AGENT_ID_KEY: key, AGENT_DATA_KEY: data}


def collect_agent_records(pairs: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
    """Build the ``/all`` payload from ``(key, raw value)`` pairs, preserving IDs.

    Skipped pairs never shift later records onto the wrong ID: each record carries its own
    key instead of relying on positional alignment (the misaligned-zip bug the legacy
    helper was extracted to fix).

    Args:
        pairs: Key/value pairs from the directory scan, in scan order.

    Returns:
        The accepted records, in input order, each shaped ``{"agent_id", "data"}``.
    """
    records: list[dict[str, Any]] = []
    for key, raw in pairs:
        record = parse_agent_record(key, raw)
        if record is not None:
            records.append(record)
    return records


def task_prompt_key(task_index: int) -> str:
    """Return the 1-based prompt-block key for a task position.

    Mirrors ``task_manager.py:2149`` (``"task_{}".format(task_id + 1)``): the stored
    conversation-details payload keys prompts ``task_1`` … ``task_n`` while the engine
    iterates tasks 0-based.

    Args:
        task_index: Zero-based task position.

    Returns:
        The ``task_<n>`` key that task's prompts are stored under.
    """
    return _TASK_PROMPT_KEY_TEMPLATE.format(number=task_index + _TASK_INDEX_OFFSET)


def select_task_prompts(prompt_responses: Mapping[str, Any], task_index: int) -> Any:  # why: free-form prompt JSON
    """Pick one task's prompt block out of a stored conversation-details payload.

    Mirrors ``task_manager.py:2154``/``2170``: a missing block answers ``None`` (callers
    degrade to empty prompts), and whatever shape was stored passes through unvalidated.

    Args:
        prompt_responses: The full stored payload (``task_<n>`` keyed).
        task_index: Zero-based task position.

    Returns:
        The stored block for that task — usually a dict — or ``None`` when absent.
    """
    return prompt_responses.get(task_prompt_key(task_index), None)


def select_multiagent_system_prompt(task_prompts: Mapping[str, Any], agent_name: str) -> Any:  # why: free-form JSON
    """Pick one sub-agent's system prompt from a multiagent task block.

    Mirrors ``task_manager.py:2157`` (``prompts[agent]["system_prompt"]``) including its
    failure mode: a missing agent or a malformed block raises exactly as the legacy
    subscript does — the multiagent ``task_1.{agent_name}.system_prompt`` shape is a
    behavior invariant of spec 0002.

    Args:
        task_prompts: One task's prompt block, keyed by sub-agent name.
        agent_name: The sub-agent whose prompt to read.

    Returns:
        The stored system prompt (a string in every well-formed payload).

    Raises:
        KeyError: When the agent or its ``system_prompt`` entry is absent (legacy parity).
        TypeError: When the block is not subscriptable (legacy parity).
    """
    return task_prompts[agent_name][_SYSTEM_PROMPT_KEY]


def _catalog_index(entries: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    """Index catalog rows by (modality, provider) for the config walk."""
    index: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for entry in entries:
        key = (str(entry.get("modality", "")), str(entry.get("provider", "")))
        index.setdefault(key, []).append(entry)
    return index


def _check_leaf(
    problems: list[str],
    index: dict[tuple[str, str], list[Mapping[str, Any]]],
    *,
    modality: str,
    provider: object,
    model: object,
    where: str,
) -> None:
    """Append a problem unless (modality, provider, model) resolves in the catalog.

    Closed rows must match exactly; `models_open` rows accept any non-empty
    model (suggestions, not a closed set). Unknown providers fail with the
    valid values — a typo must never reach the realtime path.
    """
    if not isinstance(provider, str) or not provider:
        problems.append(f"{where}: missing {modality} provider")
        return
    rows = index.get((modality, provider), [])
    if not rows:
        valid = sorted({key[1] for key in index if key[0] == modality})
        problems.append(f"{where}: unknown {modality} provider {provider!r} (valid: {', '.join(valid)})")
        return
    if not isinstance(model, str) or not model:
        problems.append(f"{where}: missing {modality} model for provider {provider!r}")
        return
    known = {str(row.get("model", "")) for row in rows}
    if model in known:
        return
    if any(row.get("models_open") is True for row in rows):
        return
    problems.append(
        f"{where}: unknown {modality} model {model!r} for provider {provider!r} (valid: {', '.join(sorted(known))})"
    )


def _check_language(
    problems: list[str], is_language_valid: Callable[[str], bool], code: object, where: str
) -> None:
    """Append a problem for a malformed BCP-47 language code (absent codes pass)."""
    if code is None:
        return
    if not isinstance(code, str) or not is_language_valid(code):
        problems.append(f"{where}: malformed language code {code!r}")


def _check_languages(
    problems: list[str],
    is_language_valid: Callable[[str], bool],
    codes: object,
    where: str,
) -> None:
    """Append problems for each malformed code in a language-hint list."""
    if codes is None:
        return
    if not isinstance(codes, list):
        problems.append(f"{where}: language hints must be a list")
        return
    for code in codes:
        _check_language(problems, is_language_valid, code, where)


def _audit_llm_leaves(
    problems: list[str],
    index: dict[tuple[str, str], list[Mapping[str, Any]]],
    node: object,
    where: str,
) -> None:
    """Recurse an `llm_agent` subtree validating every LLM call-site leaf.

    A leaf is any mapping with string `provider`/`model` keys: unlike the
    pipeline components (whose providers the schema validates), `Llm.provider`
    is a free string, so unknown providers fail here, not later.
    """
    if isinstance(node, Mapping):
        provider = node.get("provider")
        model = node.get("model")
        if isinstance(provider, str) and "model" in node:
            _check_leaf(problems, index, modality="llm", provider=provider, model=model, where=where)
        for key, value in node.items():
            _audit_llm_leaves(problems, index, value, f"{where}.{key}")
    elif isinstance(node, list):
        for position, value in enumerate(node):
            _audit_llm_leaves(problems, index, value, f"{where}[{position}]")


def audit_provider_config(
    config: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    is_language_valid: Callable[[str], bool],
) -> list[str]:
    """Audit one dumped agent config against catalog rows (pure, spec 0022 slice 2).

    Walks every task's `tools_config`: transcriber model + languages, LLM
    leaves (provider AND model — the schema leaves `Llm.provider` free), and
    the S2S model. Synthesizer `provider_config` shapes (model/voice) land in
    slice 3 with the voice curation; synthesizer/transcriber/S2S provider
    names are already schema-strict. Returns problem strings (empty means
    valid) — callers raise their own module error, so this stays import-clean:
    catalog rows arrive as plain mappings and the language predicate injects.

    Args:
        config: One agent definition dump (`model_dump`, defaults materialized).
        entries: Catalog rows as plain mappings.
        is_language_valid: BCP-47 predicate (the catalog's, injected).

    Returns:
        Human-readable problems, each naming the task path and the valid
        values; empty when the config resolves.
    """
    problems: list[str] = []
    index = _catalog_index(entries)
    tasks = config.get("tasks", [])
    if not isinstance(tasks, list):
        return ["tasks must be a list"]
    for position, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            problems.append(f"tasks[{position}]: must be a mapping")
            continue
        tools = task.get("tools_config", {})
        if not isinstance(tools, Mapping):
            problems.append(f"tasks[{position}]: tools_config must be a mapping")
            continue
        where = f"tasks[{position}]"
        transcriber = tools.get("transcriber")
        if isinstance(transcriber, Mapping):
            _check_leaf(
                problems,
                index,
                modality="asr",
                provider=transcriber.get("provider"),
                model=transcriber.get("model"),
                where=f"{where}.transcriber",
            )
            _check_language(problems, is_language_valid, transcriber.get("language"), f"{where}.transcriber")
            _check_languages(problems, is_language_valid, transcriber.get("language_hints"), f"{where}.transcriber")
        llm_agent = tools.get("llm_agent")
        if llm_agent is not None:
            _audit_llm_leaves(problems, index, llm_agent, f"{where}.llm_agent")
        s2s = tools.get("s2s")
        if isinstance(s2s, Mapping):
            provider_config = s2s.get("provider_config")
            model = provider_config.get("model") if isinstance(provider_config, Mapping) else None
            _check_leaf(
                problems, index, modality="s2s", provider=s2s.get("provider"), model=model, where=f"{where}.s2s"
            )
    return problems
