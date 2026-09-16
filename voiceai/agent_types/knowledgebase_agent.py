# legacy-shim(spec-0002) — KnowledgeBaseAgent lives in voiceai.modules.agents.brains.knowledgebase (step A6).
"""Legacy import surface for the knowledge-based (RAG) brain.

Every name the old module bound stays importable from here: the class from its new home,
and the auxiliary imports re-created verbatim (including the ``voiceai.models`` star the
old module's namespace exposed). Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

import os
import asyncio
import json
import time
from typing import List, Tuple, AsyncGenerator, Optional, Dict

from voiceai.models import *
from voiceai.agent_types.base_agent import BaseAgent
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.rag_service_client import RAGServiceClientSingleton
from voiceai.helpers.function_calling_helpers import guard_llm_base_url
from voiceai.helpers.utils import now_ms, format_messages
from voiceai.llms.types import LLMStreamChunk, LatencyData
from voiceai.providers import SUPPORTED_LLM_PROVIDERS
from voiceai.llms import OpenAiLLM
from voiceai.prompts import VOICEMAIL_DETECTION_PROMPT

from voiceai.modules.agents.brains.knowledgebase import KnowledgeBaseAgent, logger

__all__ = [
    "BaseAgent",
    "KnowledgeBaseAgent",
    "LLMStreamChunk",
    "LatencyData",
    "OpenAiLLM",
    "RAGServiceClientSingleton",
    "SUPPORTED_LLM_PROVIDERS",
    "VOICEMAIL_DETECTION_PROMPT",
    "configure_logger",
    "format_messages",
    "guard_llm_base_url",
    "logger",
    "now_ms",
]
