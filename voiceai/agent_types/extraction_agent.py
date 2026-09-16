# legacy-shim(spec-0002) — ExtractionContextualAgent lives in voiceai.modules.agents.brains.extraction (step A6).
"""Legacy import surface for the extraction brain.

Every name the old module bound stays importable from here (the auxiliary imports are
re-created verbatim). Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

from voiceai.helpers.logger_config import configure_logger

from .base_agent import BaseAgent
from voiceai.modules.agents.brains.extraction import ExtractionContextualAgent, logger

__all__ = ["BaseAgent", "ExtractionContextualAgent", "configure_logger", "logger"]
