"""Base of the brain hierarchy (spec 0002, A6; moved from `voiceai/agent_types/base_agent.py`).

Behavior-preserving verbatim move: a shared name tag every brain inherits. The legacy
per-module logger becomes the single project logger (AGENTS.md rule 3).
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.agents.constants import BASE_AGENT_NAME, MODULE_NAME

__all__ = ["BaseAgent"]

logger = get_logger(MODULE_NAME)


class BaseAgent:
    """Root class of the agent brains: carries the shared ``agent_name`` tag."""

    def __init__(self) -> None:
        self.agent_name = BASE_AGENT_NAME
