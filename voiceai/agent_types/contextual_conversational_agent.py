# legacy-shim(spec-0002) — StreamingContextualAgent lives in voiceai.modules.agents.brains.simple (step A6).
"""Legacy import surface for the simple streaming brain.

Every name the old module bound stays importable from here (the auxiliary imports are
re-created verbatim; the module-level ``load_dotenv()`` side effect now fires in the new
home this shim imports). Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

import json
import os
import time

from dotenv import load_dotenv
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.utils import format_messages
from voiceai.llms import OpenAiLLM
from voiceai.prompts import CHECK_FOR_COMPLETION_PROMPT, VOICEMAIL_DETECTION_PROMPT

from .base_agent import BaseAgent
from voiceai.modules.agents.brains.simple import StreamingContextualAgent, logger

__all__ = [
    "BaseAgent",
    "CHECK_FOR_COMPLETION_PROMPT",
    "OpenAiLLM",
    "StreamingContextualAgent",
    "VOICEMAIL_DETECTION_PROMPT",
    "configure_logger",
    "format_messages",
    "load_dotenv",
    "logger",
]
