"""Tooling schema: tool definitions, the per-task tools bundle, and the execution chain.

Moved verbatim from ``voiceai/models.py`` lines 586-609 and 682-697 (spec 0002, step A2);
only import statements changed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# §3.1 transitional allowance (declared in tests/arch/test_layer_contract.py): APIParams'
# canonical definition stays in voiceai.llms.types until the llms migration spec moves it.
from voiceai.llms.types import APIParams
from voiceai.modules.agents.models.brains import LlmAgent, SimpleLlmAgent
from voiceai.modules.agents.models.pipeline import IOModel, S2SConfig, Synthesizer, Transcriber


class ToolFunction(BaseModel):
    """A callable tool's name, purpose, and JSON-Schema parameter contract."""

    name: str = Field(..., description="The name of the tool function.")
    description: str = Field(..., description="A description of what the tool does.")
    parameters: dict = Field(..., description="JSON Schema defining the expected parameters.")
    strict: bool = Field(default=True, description="Whether to strictly enforce the parameter schema.")


class ToolDescription(BaseModel):
    """OpenAI-style tool wrapper: a type tag around a function definition."""

    type: str = Field(default="function", description="The type of the tool (usually 'function').")
    function: ToolFunction = Field(..., description="The definition of the function.")


class ToolDescriptionLegacy(BaseModel):
    """Flat legacy tool shape: name/description/parameters without the type wrapper."""

    name: str = Field(..., description="The name of the tool function.")
    description: str = Field(..., description="A description of what the tool does.")
    parameters: dict = Field(..., description="JSON Schema defining the expected parameters.")


class ToolModel(BaseModel):
    """The tools an LLM agent may call, plus the API endpoints behind them."""

    tools: str | list[ToolDescription | ToolDescriptionLegacy] | None = Field(
        default=None, description="List of tool definitions or a string reference to tools."
    )
    tools_params: dict[str, APIParams] = Field(
        ..., description="Configuration mapping for API endpoints these tools might call."
    )


class ToolsConfig(BaseModel):
    """Every pipeline component one task wires together: brain, TTS, STT, IO, and tools."""

    llm_agent: LlmAgent | SimpleLlmAgent | None = Field(
        default=None,
        description="Configuration for the LLM agent responsible for understanding and responding to user intent.",
    )
    synthesizer: Synthesizer | None = Field(
        default=None, description="Configuration for the Text-To-Speech (TTS) synthesizer."
    )
    transcriber: Transcriber | None = Field(
        default=None, description="Configuration for the Speech-To-Text (STT) transcriber."
    )
    input: IOModel | None = Field(default=None, description="Configuration for processing incoming audio streams.")
    output: IOModel | None = Field(default=None, description="Configuration for processing outgoing audio streams.")
    api_tools: ToolModel | None = Field(
        default=None, description="External API tools that the LLM agent can call during the conversation."
    )
    s2s: S2SConfig | None = Field(
        default=None,
        description="Configuration for server-to-server (S2S) multimodal audio providers (like OpenAI Realtime).",
    )
    switch_tool_description: str | None = Field(
        default=None, description="Description used when handing off to another agent in a multi-agent scenario."
    )
    switch_handoff_messages: dict[str, str] | None = Field(
        default=None, description="Messages played to the user during agent handoffs, mapped by language/intent."
    )
    agent_names: dict[str, str] | None = Field(
        default=None, description="Mapping of agent names for multi-agent dispatching."
    )


class ToolsChainModel(BaseModel):
    """How a task's tools execute: mode plus the ordered pipelines."""

    execution: str = Field(
        ..., pattern="^(parallel|sequential)$", description="Execution mode: 'parallel' or 'sequential'."
    )
    pipelines: list[list[str]] = Field(
        ..., description="A list of lists, where each sublist is a pipeline of tool names to execute."
    )
