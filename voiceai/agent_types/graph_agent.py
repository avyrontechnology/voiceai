# legacy-shim(spec-0002) — GraphAgent lives in voiceai.modules.agents.brains.graph (step A7).
"""Legacy import surface for the graph brain.

Every name the old 1,501-line module bound stays importable from here: ``GraphAgent``
and ``_DETERMINISTIC_REASONING_PREFIX`` from the split package, the module-level
routing/prompt globals from their new collaborator homes, and the auxiliary imports
re-created verbatim (including the ``voiceai.models`` star the old module's namespace
exposed). NOTE: a shim does NOT redirect monkeypatch — the pinning suites patch the
collaborator namespaces (``brains.graph.generation`` and friends) since A7. Deleted at
cutover — see the burn-down list in specs/0002-agents-module.md §Rollout.
"""

import asyncio
import json
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, AzureOpenAI, OpenAI

from voiceai.models import *
from voiceai.agent_types.base_agent import BaseAgent
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.rag_service_client import RAGServiceClientSingleton
from voiceai.helpers.function_calling_helpers import guard_llm_base_url
from voiceai.helpers.utils import (
    now_ms,
    format_messages,
    update_prompt_with_context,
    render_prompt,
    enrich_context_with_time_variables,
    get_md5_hash,
    select_message_by_language,
)
from voiceai.helpers.expression_evaluator import evaluate_edge_expression, describe_edge_expression
from voiceai.enums import EdgeConditionType, NodeType, ToolScope
from voiceai.llms.types import LLMStreamChunk, LatencyData
from voiceai.llms import OpenAiLLM
from voiceai.llms.azure_llm import should_overflow
from voiceai.llms.http_client_pool import get_shared_sync_http_client
from voiceai.providers import SUPPORTED_LLM_PROVIDERS
from voiceai.prompts import VOICEMAIL_DETECTION_PROMPT
from voiceai.constants import GPT5_MODEL_PREFIX, LANGUAGE_NAMES, canonical_model, default_reasoning_effort

from voiceai.modules.agents.brains.graph import GraphAgent, _DETERMINISTIC_REASONING_PREFIX
from voiceai.modules.agents.brains.graph.generation import logger
from voiceai.modules.agents.brains.graph.prompts import _PROMPT_VAR_PATTERN, _TIME_VAR_KEYS
from voiceai.modules.agents.brains.graph.routing import GROQ_AVAILABLE, Groq, _ROUTER_REASONING_PREFIX

__all__ = [
    "APIConnectionError",
    "APIStatusError",
    "AsyncGenerator",
    "AzureOpenAI",
    "BaseAgent",
    "EdgeConditionType",
    "GPT5_MODEL_PREFIX",
    "GROQ_AVAILABLE",
    "GraphAgent",
    "Groq",
    "LANGUAGE_NAMES",
    "LLMStreamChunk",
    "LatencyData",
    "NodeType",
    "OpenAI",
    "OpenAiLLM",
    "RAGServiceClientSingleton",
    "SUPPORTED_LLM_PROVIDERS",
    "ToolScope",
    "VOICEMAIL_DETECTION_PROMPT",
    "_DETERMINISTIC_REASONING_PREFIX",
    "_PROMPT_VAR_PATTERN",
    "_ROUTER_REASONING_PREFIX",
    "_TIME_VAR_KEYS",
    "canonical_model",
    "configure_logger",
    "default_reasoning_effort",
    "describe_edge_expression",
    "enrich_context_with_time_variables",
    "evaluate_edge_expression",
    "format_messages",
    "get_md5_hash",
    "get_shared_sync_http_client",
    "guard_llm_base_url",
    "load_dotenv",
    "logger",
    "now_ms",
    "render_prompt",
    "select_message_by_language",
    "should_overflow",
    "update_prompt_with_context",
]
