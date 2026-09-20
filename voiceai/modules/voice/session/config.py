"""Call configuration parsing, moved verbatim from ``task_manager.__init__`` (spec 0004 B4).

Region A of the legacy ``TaskManager.__init__`` (original tm 280-942) interleaved two
concerns: PURE parsing of the task/kwargs payload into per-call settings, and
side-effectful composition (queues, IO handlers, provider setup). This module owns the
first: `CallConfig.parse` computes every parsed value exactly as the legacy expressions
did — same defaults, same reference semantics (sub-dicts of ``task`` are held by
reference, never copied), same preserved quirks — and ``__init__`` consumes the result
while assigning the SAME instance attribute names, so the ``__new__`` harnesses and the
B1 construction matrix keep pinning the same surface.

Composition stays in the legacy file until B13a; nothing here touches a queue, a
handler, the event loop, or the environment. The parity suite
(tests/arch/modules/voice/session/test_config.py) proves field-for-field identity
against a real ``TaskManager`` built through the B1 matrix fixtures.

Preserved quirks (never "fixed" here — behavior-invariant checklist, R7/R12):

* ``textual_chat_agent`` reproduces the legacy dead branch that can only ever leave the
  flag ``False`` (or raise on the same malformed payloads the legacy parse raised on).
* ``end_call_primary`` is the raw ``and``-chain value — possibly the cancellation-prompt
  STRING, not a bool — because run() and the injection gate truthiness-test it.
* The completion-check prompt suffix keeps the legacy triple-quoted string's exact
  interior whitespace.
* ``stream`` uses bracket access on ``synthesizer["stream"]``: a synthesizer block
  without the key raises KeyError exactly as before.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import tzinfo
from typing import Any

import pytz

from voiceai.enums import TelephonyProvider
from voiceai.modules.voice.adapters import END_CALL_FUNCTION_PREFIX
from voiceai.modules.voice.adapters.session import (
    ACCIDENTAL_INTERRUPTION_PHRASES,
    CHECK_FOR_COMPLETION_PROMPT,
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_TIMEZONE,
    DEFAULT_USER_ONLINE_MESSAGE,
    DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION,
    RESPONSES_API_MODEL_PREFIXES,
    WEBCALL_TTS_SAMPLE_RATE,
    update_prompt_with_context,
)
from voiceai.modules.voice.static_methods import welcome_pcm_upsampled

__all__ = ["CallConfig"]


def _agent_type(task: dict[str, Any]) -> Any:  # why: raw agent_type value from a free-form legacy dict
    """Configured llm_agent type, or None for webhook and speech-to-speech tasks.

    Verbatim ``TaskManager.__agent_type`` over the task dict, so the multiagent /
    knowledgebase / graph branches below classify exactly as the legacy parse did.
    """
    if task["task_type"] == "webhook":
        return None
    return (task["tools_config"].get("llm_agent") or {}).get("agent_type", None)


def _with_context(message_config: Any, context_data: Any) -> Any:  # why: legacy configs are str or lang->str dicts
    """Substitute context variables into a message config (string or per-language dict).

    The shared body of the two verbatim substitution blocks (tm:585-594 user-online,
    tm:629-638 hangup message); each call site keeps its own legacy guard.
    """
    if isinstance(message_config, dict):
        return {lang: update_prompt_with_context(msg, context_data) for lang, msg in message_config.items()}
    return update_prompt_with_context(message_config, context_data)


def _llm_configs(
    task: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Parse the llm_agent block (tm:449-501) into (llm_config, llm_config_map, llm_agent_config).

    Reference semantics preserved: ``llm_agent_config`` is the task's own sub-dict
    (graph/knowledgebase brains and later setup read it by identity), ``llm_config`` is
    a freshly built dict (composition stamps ``buffer_size`` into it in place), and each
    multiagent map entry is a shallow ``.copy()`` with ``buffer_size`` stamped — the
    per-entry ``routes`` key is deleted later by the legacy setup loop, not here.

    Returns:
        The trio ``__init__`` assigns: ``llm_config`` is None for multiagent / s2s /
        webhook tasks, ``llm_config_map`` is empty except for multiagent,
        ``llm_agent_config`` is None whenever the legacy parse never assigned it.
    """
    llm_config: dict[str, Any] | None = None
    llm_config_map: dict[str, dict[str, Any]] = {}
    llm_agent_config: dict[str, Any] | None = None

    if _agent_type(task) == "multiagent":
        for agent, config in task["tools_config"]["llm_agent"]["llm_config"]["agent_map"].items():
            llm_config_map[agent] = config.copy()
            llm_config_map[agent]["buffer_size"] = task["tools_config"]["synthesizer"]["buffer_size"]
        return llm_config, llm_config_map, llm_agent_config

    if task["tools_config"].get("llm_agent") is None:
        return llm_config, llm_config_map, llm_agent_config

    agent_type = _agent_type(task)
    if agent_type in ("knowledgebase_agent", "graph_agent"):
        llm_agent_config = task["tools_config"]["llm_agent"]
        llm_config = {
            "model": llm_agent_config["llm_config"]["model"],
            "max_tokens": llm_agent_config["llm_config"]["max_tokens"],
            "provider": llm_agent_config["llm_config"]["provider"],
            "buffer_size": task["tools_config"]["synthesizer"].get("buffer_size"),
            "temperature": llm_agent_config["llm_config"]["temperature"],
        }
    else:
        if not agent_type:
            llm_agent_config = task["tools_config"]["llm_agent"]
        else:
            llm_agent_config = task["tools_config"]["llm_agent"]["llm_config"]

        llm_config = {
            "model": llm_agent_config["model"],
            "max_tokens": llm_agent_config["max_tokens"],
            "provider": llm_agent_config["provider"],
            "temperature": llm_agent_config["temperature"],
        }

    for key in ("reasoning_effort", "verbosity", "reasoning_summary", "thinking_budget"):
        if key in llm_agent_config:
            llm_config[key] = llm_agent_config[key]

    if llm_agent_config.get("use_responses_api") or any(
        p in llm_config.get("model", "") for p in RESPONSES_API_MODEL_PREFIXES
    ):
        llm_config["use_responses_api"] = True

    if llm_agent_config.get("compact_threshold"):
        llm_config["compact_threshold"] = llm_agent_config["compact_threshold"]

    return llm_config, llm_config_map, llm_agent_config


def _welcome_audio(
    task: dict[str, Any],
    kwargs: Mapping[str, Any],
    is_web_based_call: Any,  # why: truthiness-tested kwargs value, exactly as legacy
) -> tuple[Any, int, bytes | None]:
    """Parse the welcome audio trio (tm:295-319): raw b64, source rate, decoded PCM.

    ``__init__`` still POPS the two kwargs keys (the kwargs contract hides the blob from
    downstream components); this reads the same values without mutating anything. The
    upsample is memoized process-wide (``welcome_pcm_upsampled``), so parsing here hits
    the exact cache the legacy call site used.

    Returns:
        ``(welcome_message_audio, welcome_message_audio_sample_rate,
        preloaded_welcome_audio)``.
    """
    welcome_message_audio = kwargs.get("welcome_message_audio", None)
    # Rate the backend synthesized the welcome at: 8000 for telephony (unchanged legacy
    # payloads too), 24000 for web calls so the first turn matches the full-band TTS.
    welcome_message_audio_sample_rate = int(kwargs.get("welcome_message_audio_sample_rate", None) or 8000)

    # Pre-decode welcome audio for faster playback
    preloaded_welcome_audio = base64.b64decode(welcome_message_audio) if welcome_message_audio else None
    # Cached welcome PCM may be below the raw-PCM output rate (web/freeswitch play at 24kHz), so
    # upsample or the first audio is pitched; already-24kHz welcomes pass through. Memoized per
    # welcome (welcome_pcm_upsampled) so this resample runs once, not in every call's __init__ —
    # otherwise a burst of concurrent calls spikes CPU on the event loop.
    is_freeswitch_output = (task.get("tools_config", {}).get("output") or {}).get(
        "provider"
    ) == TelephonyProvider.FREESWITCH.value
    if (
        (is_web_based_call or is_freeswitch_output)
        and preloaded_welcome_audio
        and welcome_message_audio_sample_rate != WEBCALL_TTS_SAMPLE_RATE
    ):
        preloaded_welcome_audio = welcome_pcm_upsampled(
            welcome_message_audio, WEBCALL_TTS_SAMPLE_RATE, welcome_message_audio_sample_rate
        )
    return welcome_message_audio, welcome_message_audio_sample_rate, preloaded_welcome_audio


def _completion_prompt(conversation_config: dict[str, Any], use_llm_to_determine_hangup: Any) -> Any:
    """Build the completion-check prompt (tm:616-626), or None when LLM hangup is off.

    The JSON-format suffix keeps the legacy triple-quoted literal's exact interior
    whitespace: downstream prompt hashing and QA transcripts see identical bytes.
    """
    check_for_completion_prompt = None
    if use_llm_to_determine_hangup:
        check_for_completion_prompt = conversation_config.get("call_cancellation_prompt", None)
        if not check_for_completion_prompt:
            check_for_completion_prompt = CHECK_FOR_COMPLETION_PROMPT
        check_for_completion_prompt += """
                        Respond only in this JSON format:
                            {{
                              "hangup": "Yes" or "No"
                            }}
                    """
    return check_for_completion_prompt


def _end_call_settings(
    task: dict[str, Any],
    conversation_config: dict[str, Any],
    use_llm_to_determine_hangup: Any,  # why: raw truthy task-config value, part of the and-chain
    is_s2s: bool,
) -> tuple[str | None, Any, list[Any]]:
    """Parse the end_call tool decision trio (tm:641-688, minus the kwargs injection).

    ``__init__`` keeps the actual ``_inject_end_call_tool`` calls (they mutate
    ``self.kwargs``); this computes the description (with the s2s no-goodbye variant
    REPLACING the base one, verbatim), the raw ``end_call_primary`` and-chain value,
    and the graph nodes opting in via ``function_call="end_call"``.

    Returns:
        ``(end_call_description, end_call_primary, end_call_nodes)``.
    """
    cancellation_prompt = conversation_config.get("call_cancellation_prompt")
    end_call_description = (
        (
            "End the current call. Always say your goodbye message before calling this function.\n"
            f"Criteria for when to end: {cancellation_prompt}"
        )
        if cancellation_prompt
        else None
    )

    if is_s2s:
        # The end_call result asks for the goodbye, so asking for one here too
        # would have the model say it twice.
        end_call_description = (
            "End the current call. Do not say goodbye before calling this "
            "function; you will be prompted to say it afterwards."
            + (f"\nCriteria for when to end: {cancellation_prompt}" if cancellation_prompt else "")
        )

    end_call_primary = (
        conversation_config.get("end_call_tool_mode") in ("primary", "primary_with_shadow_hangup")
        and use_llm_to_determine_hangup
        and cancellation_prompt
    )

    end_call_nodes: list[Any] = []
    if _agent_type(task) == "graph_agent":
        # a node opting in via function_call="end_call" needs the tool regardless of the hangup toggle
        llm_config = task["tools_config"]["llm_agent"].get("llm_config", {}) or {}
        end_call_nodes = [
            n.get("id")
            for n in (llm_config.get("nodes") or [])
            if n.get("function_call") == END_CALL_FUNCTION_PREFIX and n.get("id")
        ]
    return end_call_description, end_call_primary, end_call_nodes


@dataclass(frozen=True)
class CallConfig:
    """Every parsed per-call setting Region A derived, one field per instance attribute.

    Field names match the ``TaskManager`` attributes ``__init__`` assigns from them
    (the ``__new__`` harnesses hand-set those same names), except the few values that
    were locals in the legacy body (``dtmf_enabled``, ``process_interim_results``,
    ``end_call_description``, ``end_call_nodes``) — ``__init__`` consumes those at the
    same points the locals lived. Dict/list fields hold REFERENCES into the task dict
    wherever the legacy parse did; a fresh container is built only where the legacy
    parse built one (``llm_config``, the multiagent map entries, the phrase set).
    """

    timezone: tzinfo
    language: str
    transfer_call_params: Any  # why: opaque caller-supplied kwargs payload
    s2s_config: Any  # why: free-form s2s block from the task dict, or None
    enforce_streaming: Any  # why: raw kwargs value, truthiness-used by legacy code
    room_url: Any  # why: raw kwargs value
    is_web_based_call: Any  # why: raw kwargs value, truthiness-used by legacy code
    run_id: Any  # why: raw kwargs value (uuid string in practice)
    pipelines: Any  # why: the task's own toolchain list, held by reference
    textual_chat_agent: bool
    sampling_rate: int
    welcome_message_audio: Any  # why: raw kwargs value (b64 string in practice)
    welcome_message_audio_sample_rate: int
    welcome_message_delay: Any  # why: raw task-config value
    preloaded_welcome_audio: bytes | None
    language_injection_mode: Any  # why: raw task-config value
    language_instruction_template: Any  # why: raw task-config value
    stream: Any  # why: legacy and-chain result; falsy synthesizer["stream"] values pass through
    llm_config: dict[str, Any] | None
    llm_config_map: dict[str, dict[str, Any]]
    llm_agent_config: dict[str, Any] | None
    conversation_config: dict[str, Any]
    synthesizer_voice: Any  # why: raw provider_config value
    dtmf_enabled: Any  # why: raw task-config value, truthiness-used
    trigger_user_online_message_after: Any  # why: raw task-config value
    check_if_user_online: Any  # why: raw task-config value
    check_user_online_message_config: Any  # why: legacy config is str or lang->str dict
    process_interim_results: str
    minimum_wait_duration: Any  # why: transcriber endpointing value, or None
    incremental_delay: Any  # why: raw task-config value
    hang_conversation_after: Any  # why: raw task-config value
    use_fillers: Any  # why: raw task-config value
    use_llm_to_determine_hangup: Any  # why: raw task-config value, truthiness-used
    check_for_completion_prompt: Any  # why: prompt string, or None when LLM hangup is off
    call_hangup_message_config: Any  # why: legacy config is str or lang->str dict, or None
    end_call_description: str | None
    end_call_primary: Any  # why: preserved quirk — raw and-chain value, may be the prompt STRING
    end_call_nodes: list[Any]
    number_of_words_for_interruption: Any  # why: raw task-config value
    accidental_interruption_phrases: set[str]
    should_backchannel: Any  # why: legacy and-chain result, truthiness-used
    backchanneling_start_delay: Any  # why: raw task-config value
    backchanneling_message_gap: Any  # why: raw task-config value
    discard_pre_welcome_utterance: Any  # why: raw task-config value
    switch_handoff_messages: Any  # why: the task's own dict by reference, or a fresh {}
    agent_names: Any  # why: the task's own dict by reference, or a fresh {}

    @classmethod
    def parse(
        cls,
        *,
        task: dict[str, Any],
        context_data: Any,  # why: opaque caller-supplied context payload, or None
        kwargs: Mapping[str, Any],
        turn_based_conversation: Any,  # why: raw constructor value, truthiness-used by legacy code
    ) -> CallConfig:
        """Parse one task payload into the per-call configuration, verbatim to Region A.

        Read-only over every input: ``kwargs`` mutations (pops, api_tools injection)
        and every ``task_id == 0`` gate stay in ``__init__`` — the parsed values
        themselves are task-shaped, so they parse identically for every task.

        Args:
            task: The task payload (``tools_config`` / ``toolchain`` / ``task_config``),
                held by reference — sub-dicts are shared with the caller on purpose.
            context_data: Recipient context for prompt substitution, or None.
            kwargs: The constructor's ``**kwargs`` view, BEFORE ``__init__`` pops the
                welcome keys.
            turn_based_conversation: The legacy constructor flag (dashboard sessions).

        Returns:
            The parsed configuration; every field documented on the class.
        """
        enforce_streaming = kwargs.get("enforce_streaming", False)
        is_web_based_call = kwargs.get("is_web_based_call", False)
        s2s_config = task["tools_config"].get("s2s")
        # Speech-to-speech classification, verbatim __is_s2s over the parsed values.
        is_s2s = bool(s2s_config) and task["task_type"] == "conversation"

        # spec-0004 B4: preserved quirk — the legacy branch (tm:254-259) could only ever
        # re-assign False; reproduced including its KeyError behavior on the same inputs.
        textual_chat_agent = False
        if (
            task["toolchain"]["pipelines"][0] == "llm"
            and task["tools_config"]["llm_agent"]["agent_task"] == "conversation"
        ):
            textual_chat_agent = False

        welcome_message_audio, welcome_sample_rate, preloaded_welcome_audio = _welcome_audio(
            task, kwargs, is_web_based_call
        )

        llm_config, llm_config_map, llm_agent_config = _llm_configs(task)

        conversation_config: dict[str, Any] = task.get("task_config", {})

        check_user_online_message_config = conversation_config.get(
            "check_user_online_message", DEFAULT_USER_ONLINE_MESSAGE
        )
        if check_user_online_message_config and context_data:
            check_user_online_message_config = _with_context(check_user_online_message_config, context_data)

        call_hangup_message_config = conversation_config.get("call_hangup_message", None)
        if call_hangup_message_config and context_data and not is_web_based_call:
            call_hangup_message_config = _with_context(call_hangup_message_config, context_data)

        use_llm_to_determine_hangup = conversation_config.get("hangup_after_LLMCall", False)
        end_call_description, end_call_primary, end_call_nodes = _end_call_settings(
            task, conversation_config, use_llm_to_determine_hangup, is_s2s
        )

        synthesizer_config = task["tools_config"].get("synthesizer") or {}
        provider_config = synthesizer_config.get("provider_config") or {}

        return cls(
            timezone=pytz.timezone(DEFAULT_TIMEZONE),
            language=DEFAULT_LANGUAGE_CODE,
            transfer_call_params=kwargs.get("transfer_call_params", None),
            s2s_config=s2s_config,
            enforce_streaming=enforce_streaming,
            room_url=kwargs.get("room_url", None),
            is_web_based_call=is_web_based_call,
            run_id=kwargs.get("run_id"),
            pipelines=task["toolchain"]["pipelines"],
            textual_chat_agent=textual_chat_agent,
            sampling_rate=24000,  # initial rate; output-handler composition may re-stamp it
            welcome_message_audio=welcome_message_audio,
            welcome_message_audio_sample_rate=welcome_sample_rate,
            welcome_message_delay=task.get("task_config", {}).get("welcome_message_delay", 0),
            preloaded_welcome_audio=preloaded_welcome_audio,
            language_injection_mode=task["task_config"].get("language_injection_mode"),
            language_instruction_template=task["task_config"].get("language_instruction_template"),
            stream=(task["tools_config"]["synthesizer"] is not None and task["tools_config"]["synthesizer"]["stream"])
            and (enforce_streaming or not turn_based_conversation),
            llm_config=llm_config,
            llm_config_map=llm_config_map,
            llm_agent_config=llm_agent_config,
            conversation_config=conversation_config,
            synthesizer_voice=provider_config.get("voice"),
            dtmf_enabled=conversation_config.get("dtmf_enabled", False),
            trigger_user_online_message_after=conversation_config.get(
                "trigger_user_online_message_after", DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION
            ),
            check_if_user_online=conversation_config.get("check_if_user_online", True),
            check_user_online_message_config=check_user_online_message_config,
            process_interim_results=("true" if conversation_config.get("optimize_latency", False) is True else "false"),
            minimum_wait_duration=(task["tools_config"].get("transcriber") or {}).get("endpointing"),
            incremental_delay=conversation_config.get("incremental_delay", 100),
            hang_conversation_after=conversation_config.get("hangup_after_silence", 10),
            use_fillers=conversation_config.get("use_fillers", False),
            use_llm_to_determine_hangup=use_llm_to_determine_hangup,
            check_for_completion_prompt=_completion_prompt(conversation_config, use_llm_to_determine_hangup),
            call_hangup_message_config=call_hangup_message_config,
            end_call_description=end_call_description,
            end_call_primary=end_call_primary,
            end_call_nodes=end_call_nodes,
            number_of_words_for_interruption=conversation_config.get("number_of_words_for_interruption", 3),
            accidental_interruption_phrases=set(ACCIDENTAL_INTERRUPTION_PHRASES),
            should_backchannel=conversation_config.get("backchanneling", False) and not is_s2s,
            backchanneling_start_delay=conversation_config.get("backchanneling_start_delay", 5),
            backchanneling_message_gap=conversation_config.get("backchanneling_message_gap", 2),
            discard_pre_welcome_utterance=conversation_config.get("discard_pre_welcome_utterance", False),
            switch_handoff_messages=task.get("tools_config", {}).get("switch_handoff_messages") or {},
            agent_names=task.get("tools_config", {}).get("agent_names") or {},
        )
