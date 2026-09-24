"""Offline helper: audit stored agent configs against the catalog (spec 0022, slice 2).

Report-only by design (grandfather rule): existing rows keep running however
they look; this names the rows whose next update must validate clean. The live
collection loop lives in the spec runbook (like every backfill tool): this
module holds the pure audit over plain mappings, so both the agents service
and the operator script share one implementation.

Offline only: no network, no credentials. Importing this module has no side
effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from voiceai.modules.agents.static_methods import audit_provider_config
from voiceai.modules.catalog.static_methods import is_valid_language

__all__ = ["audit_many"]


def audit_many(
    documents: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    is_language_valid: Callable[[str], bool] | None = None,
) -> dict[str, list[str]]:
    """Audit stored agent documents, returning problems keyed by agent id.

    Args:
        documents: Stored `agents` rows (driver shape; `config` holds the dump).
        entries: Catalog rows as plain mappings.
        is_language_valid: BCP-47 predicate (defaults to the catalog's own).

    Returns:
        `{agent_id: [problems]}` for invalid rows only; valid rows absent.
        Documents without an id key under their `agent_id` (or missing id).
    """
    predicate = is_language_valid or is_valid_language
    report: dict[str, list[str]] = {}
    for document in documents:
        config = document.get("config")
        if not isinstance(config, Mapping):
            continue
        problems = audit_provider_config(config, entries, predicate)
        if problems:
            key = document.get("agent_id", document.get("id", "?"))
            report[str(key)] = problems
    return report
