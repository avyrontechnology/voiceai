# legacy-shim(spec-0002) — SummarizationContextualAgent lives in voiceai.modules.agents.brains.summarization (step A6).
"""Legacy import surface for the summarization brain.

Every name the old module bound stays importable from here (the auxiliary imports are
re-created verbatim). Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

from voiceai.helpers.logger_config import configure_logger

from .base_agent import BaseAgent
from voiceai.modules.agents.brains.summarization import SummarizationContextualAgent, logger

__all__ = ["BaseAgent", "SummarizationContextualAgent", "configure_logger", "logger"]
