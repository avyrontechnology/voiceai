"""Legacy runtime collaborators the graph brain still leans on (spec 0002, A7).

This file is a §3.1 bridge (rule 1), the graph-specific sibling of ``adapters/brains.py``:
the modules under ``voiceai/modules/agents/brains/graph/`` (and ``brains/legacy_graph.py``)
import THESE names, so they stay free of legacy import statements themselves. Every import
is tagged with the migration that retires it. The re-exports are static on purpose: the
legacy ``graph_agent.py`` bound these names statically at import time too, so monkeypatch
semantics are unchanged (the pinning suites patch the graph submodules' attributes, never
these).
"""

from __future__ import annotations

# §3.1 bridge imports — retire with the llms/platform migration spec ("specs to follow"
# per spec 0002 non-goals): model canonicalization and the reasoning/language tables.
from voiceai.constants import (
    GPT5_MODEL_PREFIX,
    LANGUAGE_NAMES,
    canonical_model,
    default_reasoning_effort,
)

# §3.1 bridge imports — retire with spec 0004 (helpers migration): expression-edge
# evaluation and its human-readable trace.
from voiceai.helpers.expression_evaluator import describe_edge_expression, evaluate_edge_expression

# §3.1 bridge imports — retire with spec 0004 (helpers migration): prompt/context
# rendering, the audio-cache hash, and static-message language selection.
from voiceai.helpers.utils import (
    enrich_context_with_time_variables,
    get_md5_hash,
    render_prompt,
    select_message_by_language,
    update_prompt_with_context,
)

__all__ = [
    "GPT5_MODEL_PREFIX",
    "LANGUAGE_NAMES",
    "canonical_model",
    "default_reasoning_effort",
    "describe_edge_expression",
    "enrich_context_with_time_variables",
    "evaluate_edge_expression",
    "get_md5_hash",
    "render_prompt",
    "select_message_by_language",
    "update_prompt_with_context",
]
