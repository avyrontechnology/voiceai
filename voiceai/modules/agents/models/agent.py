"""Agent-level schema: the top-level agent models.

Shared leaves (welcome text, localized text, validators) live in
``models/base.py`` (spec 0019 broke the ``agent → tools → brains → agent``
cycle there); this module re-exports them so existing ``models.agent.X``
import paths keep resolving.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from voiceai.modules.agents.models.base import (
    AGENT_WELCOME_MESSAGE,
    LocalizedText,
    validate_attribute,
    validate_reasoning_effort_for_model,
)

__all__ = [
    "AGENT_WELCOME_MESSAGE",
    "AgentModel",
    "ConversationConfig",
    "LocalizedText",
    "Task",
    "validate_attribute",
    "validate_reasoning_effort_for_model",
]


# ``tools`` (needed only by ``Task`` below) transitively imports ``pipeline``
# and ``brains``; all three now resolve through the leaf ``models/base.py``.
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
