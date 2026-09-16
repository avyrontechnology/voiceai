# legacy-shim(spec-0002) — BaseAgent lives in voiceai.modules.agents.brains.base (step A6).
"""Legacy import surface for the brain base class.

Every name the old module bound stays importable from here (the auxiliary imports are
re-created verbatim). Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

from voiceai.helpers.logger_config import configure_logger

from voiceai.modules.agents.brains.base import BaseAgent, logger

__all__ = ["BaseAgent", "configure_logger", "logger"]
