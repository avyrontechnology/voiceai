"""Legacy runtime collaborators the six easy brains still lean on (spec 0002, A6).

This file is a §3.1 bridge (rule 1): the ONLY place the brains may reach legacy code
from — the brain modules under ``voiceai/modules/agents/brains/`` import THESE names, so
they stay free of legacy import statements themselves. Every import is tagged with the
migration that retires it. The re-exports are static on purpose: the legacy brains bound
these names statically at import time too, so monkeypatch semantics are unchanged (and
the brains suite patches the BRAIN module's attribute via ``__module__``, never these).
"""

from __future__ import annotations

# §3.1 bridge imports — retire with spec 0004 (helpers migration): the outbound-URL
# guard, the RAG service client, prompt formatting, and the millisecond clock.
from voiceai.helpers.function_calling_helpers import guard_llm_base_url
from voiceai.helpers.rag_service_client import RAGServiceClientSingleton
from voiceai.helpers.utils import format_messages, now_ms

# §3.1 bridge import — retires with the llms migration spec ("specs to follow" per
# spec 0002 non-goals): the packaged default voicemail-detection prompt.
from voiceai.prompts import VOICEMAIL_DETECTION_PROMPT

# §3.1 bridge import — retires with the llms/providers migration spec: the
# provider-name → LLM-class dispatch table the knowledgebase brain builds from.
from voiceai.providers import SUPPORTED_LLM_PROVIDERS

__all__ = [
    "RAGServiceClientSingleton",
    "SUPPORTED_LLM_PROVIDERS",
    "VOICEMAIL_DETECTION_PROMPT",
    "format_messages",
    "guard_llm_base_url",
    "now_ms",
]
