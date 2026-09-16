"""Agent-level schema: welcome message, shared validators, and the top-level agent models.

Moved verbatim from ``voiceai/models.py`` lines 21-41 and 700-739 (spec 0002, step A2);
only import statements changed. The module is split around a deliberately late import:
the shared helpers defined at the top are imported by ``pipeline``/``brains``, while
``Task`` below needs ``tools`` — which itself imports ``pipeline`` and ``brains``. The
package ``__init__`` imports this module first, so the cycle always resolves with the
helpers already bound.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel, Field, field_validator

from voiceai.modules.agents.constants import MODEL_REASONING_EFFORT_MAP

AGENT_WELCOME_MESSAGE = "This call is being recorded for quality assurance and training. Please speak now."

# A message that is either a single string or a per-language {lang_code: text} map.
LocalizedText = str | dict[str, str]

_T = TypeVar("_T")


def validate_attribute(value: _T, allowed_values: Sequence[str], value_type: str = "provider") -> _T:
    """Reject a value outside its allowed set with the legacy error text.

    Args:
        value: The candidate value (typically a provider name).
        allowed_values: Every accepted value.
        value_type: Label used in the error message.

    Returns:
        The value unchanged when it is allowed.

    Raises:
        ValueError: When the value is not in ``allowed_values``.
    """
    if value not in allowed_values:
        raise ValueError(f"Invalid value for {value_type}:'{value}' provided. Supported values: {allowed_values}.")
    return value


def validate_reasoning_effort_for_model(model: str, reasoning_effort: str) -> None:
    """Reject a reasoning effort a GPT model does not support; non-GPT models pass through.

    Args:
        model: Model name, optionally provider-prefixed ("azure/gpt-5").
        reasoning_effort: The requested effort value.

    Raises:
        ValueError: When the model's supported-effort list exists and excludes the value.
    """
    if "gpt" not in model:
        return

    if "/" in model:
        model = model.split("/")[-1]

    supported = MODEL_REASONING_EFFORT_MAP.get(model, None)
    if supported is not None and reasoning_effort not in supported:
        raise ValueError(f"reasoning_effort '{reasoning_effort}' is not supported for model '{model}'.")


# Late on purpose: ``tools`` (needed only by ``Task`` below) transitively imports
# ``pipeline`` and ``brains``, which import the helpers defined above — see the module
# docstring for the cycle contract.
from voiceai.modules.agents.models.tools import ToolsChainModel, ToolsConfig  # noqa: E402


class ConversationConfig(BaseModel):
    """Per-task conversation behavior: latency, interruption, silence, and hangup policy."""

    optimize_latency: bool | None = Field(
        default=True, description="Whether to aggressively optimize for lower latency across the pipeline."
    )
    hangup_after_silence: int | None = Field(
        default=20, description="Time in seconds of silence before the system automatically hangs up the call."
    )
    incremental_delay: int | None = Field(
        default=900, description="Incremental delay in milliseconds used to handle long pauses in conversation."
    )
    number_of_words_for_interruption: int | None = Field(
        default=1, description="Minimum number of words detected before triggering a barge-in/interruption."
    )
    interruption_backoff_period: int | None = Field(
        default=100, description="Time in milliseconds to ignore further audio immediately after an interruption."
    )
    hangup_after_LLMCall: bool | None = Field(
        default=False, description="Whether to automatically hang up after the LLM agent completes its primary goal."
    )
    call_cancellation_prompt: str | None = Field(
        default=None, description="Prompt/instruction used to detect if the user wants to cancel or end the call."
    )
    backchanneling: bool | None = Field(
        default=False,
        description="Enable active listening/backchanneling (e.g., saying 'mm-hmm' while the user speaks).",
    )
    backchanneling_message_gap: int | None = Field(
        default=5, description="Minimum gap in seconds between consecutive backchanneling messages."
    )
    backchanneling_start_delay: int | None = Field(
        default=5, description="Delay in seconds before initiating backchanneling behavior."
    )
    ambient_noise: bool | None = Field(
        default=False, description="Whether to play synthetic ambient noise in the background."
    )
    call_terminate: int | None = Field(
        default=90, description="Maximum total call duration in seconds before forced termination."
    )
    use_fillers: bool | None = Field(
        default=False,
        description="Whether to use filler words ('uh', 'um') before LLM responses to reduce perceived latency.",
    )
    trigger_user_online_message_after: int | None = Field(
        default=10,
        description="Time in seconds of inactivity before prompting the user to see if they are still there.",
    )
    check_user_online_message: str | dict[str, str] | None = Field(
        default="Hey, are you still there", description="The message played when checking if the user is still online."
    )
    check_if_user_online: bool | None = Field(
        default=True, description="Enable proactive checks to see if the user is still on the line."
    )
    dtmf_enabled: bool | None = Field(default=False, description="Whether to enable processing of DTMF (keypad) tones.")
    voicemail: bool | None = Field(default=False, description="Whether to enable voicemail detection.")
    voicemail_detection_duration: float | None = Field(
        default=30.0, description="Time window in seconds to detect voicemail signals."
    )
    voicemail_check_interval: float | None = Field(
        default=7.0, description="Minimum time in seconds between interim voicemail checks."
    )
    voicemail_min_transcript_length: int | None = Field(
        default=7, description="Minimum number of transcribed words to trigger an interim voicemail check."
    )

    @field_validator("hangup_after_silence", mode="before")
    def set_hangup_after_silence(cls, v: int | None) -> int:
        """Fold an explicit ``None`` into the legacy fallback of 10 seconds."""
        return v if v is not None else 10  # Set default value if None is passed


class Task(BaseModel):
    """One unit of agent work: a tool configuration plus its execution chain."""

    tools_config: ToolsConfig = Field(
        ..., description="Configuration mapping for tools, STT, TTS, and the LLM agent used in this task."
    )
    toolchain: ToolsChainModel = Field(
        ..., description="Execution pipeline and chain for the tasks (e.g., parallel vs sequential)."
    )
    task_type: str | None = Field(
        default="conversation", description="Type of the task. E.g., 'conversation', 'extraction', 'summarization'."
    )
    # legacy-parity(spec-0002): default_factory=dict leaves an omitted task_config as a plain
    # {} at runtime (pydantic does not validate defaults) — preserved verbatim, typing quirk included.
    task_config: ConversationConfig = Field(  # type: ignore[assignment]
        default_factory=dict,
        description="Conversation settings, including latency optimizations and termination logic.",
    )


class AgentModel(BaseModel):
    """The full authoring-time definition of one agent."""

    agent_name: str = Field(..., description="A recognizable name for this agent.")
    agent_type: str = Field(
        default="other", description="Type of agent architecture. E.g., 'other', 'graph_agent', 'llm_agent'."
    )
    tasks: list[Task] = Field(
        ..., description="List of tasks to execute in order. Can include conversations, extractions, etc."
    )
    agent_welcome_message: str | None = Field(
        default=AGENT_WELCOME_MESSAGE, description="First message spoken by the agent upon connecting the call."
    )
