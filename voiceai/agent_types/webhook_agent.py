# legacy-shim(spec-0002) — WebhookAgent lives in voiceai.modules.agents.brains.webhook (step A6).
"""Legacy import surface for the webhook brain.

Every name the old module bound stays importable from here (the auxiliary imports are
re-created verbatim). Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

import aiohttp

from voiceai.helpers.logger_config import configure_logger

from .base_agent import BaseAgent
from voiceai.modules.agents.brains.webhook import WebhookAgent, logger

__all__ = ["BaseAgent", "WebhookAgent", "configure_logger", "logger"]
