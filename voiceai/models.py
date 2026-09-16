# legacy-shim(spec-0002) — superset re-export; the schema lives in voiceai.modules.agents.models (step A2).
"""Legacy import surface for the agent-definition schema (spec 0002, R6).

Every name the old 739-line module exposed stays importable from here: the schema itself
(now ``voiceai.modules.agents.models``), the engine classes pulled in through
``voiceai.providers``, the enums, and the stdlib/typing/pydantic names that star-import
consumers (quickstart server, agent_types, assistant) picked up transitively. A snapshot
canary in ``tests/arch/modules/agents/`` pins the superset. Deleted at cutover — see the
burn-down list in specs/0002-agents-module.md §Rollout.
"""

import json
from typing import Any, Literal, Optional, List, Union, Dict, Callable
from pydantic import BaseModel, Field, field_validator, ValidationError, Json, model_validator
from pydantic_core import PydanticCustomError
from .providers import *
from .enums import (
    TelephonyProvider,
    SynthesizerProvider,
    TranscriberProvider,
    S2SProvider,
    ReasoningEffort,
    Verbosity,
    ExpressionOperator,
    ExpressionLogic,
    EdgeConditionType,
    NodeType,
    VariableType,
)
from .constants import MODEL_REASONING_EFFORT_MAP
from voiceai.llms.types import APIParams
from voiceai.modules.agents.models import *
