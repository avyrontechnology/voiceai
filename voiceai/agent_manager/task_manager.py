import asyncio
import audioop
from collections import defaultdict
from functools import lru_cache
from datetime import datetime
import io
import math
import os
import random
import re
import traceback
import time
import json
import uuid
import copy
import base64
import pytz
import websockets

import aiohttp
from pydub import AudioSegment

from voiceai.constants import (
    ACCIDENTAL_INTERRUPTION_PHRASES,
    DEFAULT_USER_ONLINE_MESSAGE,
    DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION,
    FILLER_DICT,
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_TIMEZONE,
    LANGUAGE_NAMES,
    LANGUAGE_SWITCH_AUDIO_GAP_S,
    LANGUAGE_SWITCH_DECIDE_TIMEOUT_S,
    LANGUAGE_SWITCH_MAX_HOLD_S,
    LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S,
    LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S,
    LANGUAGE_SWITCH_SETTLE_MS,
    LLM_DEFAULT_CONFIGS,
    LLM_FIRST_CHUNK_TIMEOUT_S,
    LLM_REGEN_SETTLE_S,
    REGEN_SETTLE_EXCLUDED_TRANSCRIBERS,
    NON_EVIDENCE_MARK_TYPES,
    SWITCH_LANGUAGE_TOOL_DEFINITION,
    END_CALL_FUNCTION_PREFIX,
    END_CALL_TOOL_DEFINITION,
    RESPONSES_API_MODEL_PREFIXES,
    S2S_GOODBYE_TIMEOUT_S,
    S2S_STREAM_SID_TIMEOUT_S,
    STALL_HANGUP_FLOOR_S,
    STUCK_AUDIO_GATE_RELEASE_S,
    WEB_BASED_CALL_PROVIDER,
    WEBCALL_TTS_SAMPLE_RATE,
)
from voiceai.helpers.function_calling_helpers import (
    trigger_api,
    computed_api_response,
)
from voiceai.helpers.conversation_history import ConversationHistory
from .base_manager import BaseManager
from .interruption_manager import InterruptionManager
from voiceai.agent_types import *
from voiceai.providers import *
from voiceai.s2s import events as s2s_events
from voiceai.enums import (
    TelephonyProvider,
    LogComponent,
    LogDirection,
    HangupReason,
    NodeType,
    ChatRole,
    ToolScope,
)
from voiceai.exceptions import VoiceAIComponentError, LLMError, SynthesizerError, TranscriberError
from voiceai.prompts import *
from voiceai.helpers.language_detector import LanguageDetector
from voiceai.helpers.language_switcher import LanguageSwitcher
from voiceai.transcriber.transcriber_pool import TranscriberPool
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.helpers.utils import (
    structure_system_prompt,
    compute_function_pre_call_message,
    select_message_by_language,
    get_date_time_from_timezone,
    calculate_audio_duration,
    create_ws_data_packet,
    get_file_names_in_directory,
    get_raw_audio_bytes,
    get_synth_audio_format,
    is_valid_md5,
    get_required_input_types,
    format_messages,
    safe_log_text,
    get_prompt_responses,
    resample,
    save_audio_file_to_s3,
    update_prompt_with_context,
    get_md5_hash,
    static_node_audio_key,
    clean_json_string,
    wav_bytes_to_pcm,
    mp3_bytes_to_pcm,
    convert_to_request_log,
    yield_chunks_from_memory,
    process_task_cancellation,
    audio_to_mulaw8k,
    audio_to_pcm,
    pcm_to_ulaw,
    ulaw_to_pcm,
    format_error_message,
    enrich_context_with_time_variables,
)
from voiceai.helpers.logger_config import configure_logger
from ..helpers.mark_event_meta_data import MarkEventMetaData
from ..helpers.observable_variable import ObservableVariable
from voiceai.models import S2SConfig
# spec-0004 B3: `.models` is now a shim over voiceai.modules.voice.models; the `as`
# spelling keeps this module an explicit re-exporter for mypy-checked consumers.
from .models import ComponentLatencies as ComponentLatencies
from .voicemail_handler import VoicemailHandler

logger = configure_logger(__name__)


# spec-0004 B3: the module-level pure functions previously defined here (tm 124-272) live
# in voiceai.modules.voice.static_methods. These same-named bindings are the delegators
# the migration contract requires: THIS module stays the lookup/patch site
# (`voiceai.agent_manager.task_manager.<name>` keeps resolving for imports AND for
# monkeypatch string paths), and welcome_pcm_upsampled keeps its one shared lru_cache
# because the binding is the memoized function object itself.
# The `as` spelling makes each binding an EXPLICIT re-export, so mypy-checked consumers
# (tests/arch) may keep importing these names from this module (`no_implicit_reexport`).
from voiceai.modules.voice.static_methods import _inject_end_call_tool as _inject_end_call_tool
from voiceai.modules.voice.static_methods import asr_id_to_int as asr_id_to_int
from voiceai.modules.voice.static_methods import build_lid_decision_record as build_lid_decision_record
from voiceai.modules.voice.static_methods import is_alphanumeric_readout as is_alphanumeric_readout
from voiceai.modules.voice.static_methods import trailing_utterance_text as trailing_utterance_text
from voiceai.modules.voice.static_methods import welcome_pcm_upsampled as welcome_pcm_upsampled

# spec-0004 B4: Region A's pure parsing (original tm 280-942) lives in the voice module's
# CallConfig; __init__ consumes it below while ASSIGNING THE SAME instance attribute
# names, so the __new__ harnesses and the B1 construction matrix keep pinning the same
# surface. Only the session package's __init__ public surface is imported (§3.1).
from voiceai.modules.voice.session import CallConfig

# spec-0004 B5: Region U (the "Speech-to-speech conversation" banner below) lives in
# voiceai.modules.voice.session.s2s_runner. TaskManager keeps a same-named thin
# delegator per moved method and injects itself (the S2SSession facade) on every call
# (§3.1 bridge 3). The RUNNER module is now the lookup site for the S2S bodies'
# globals (convert_to_request_log, trigger_api, s2s_events, ...): monkeypatch string
# paths for those target voiceai.modules.voice.session.s2s_runner.<name>.
from voiceai.modules.voice.session import s2s_runner as _voice_s2s_runner

# spec-0004 B6: Region E (runtime prompt loading — load_prompt and its three private
# bodies) lives VERBATIM in voiceai.modules.voice.session.prompts; TaskManager keeps
# same-named thin delegators below and injects itself (the PromptSession facade) on
# every call (§3.1 bridge 3). The PROMPTS module is now the lookup site for those
# bodies' globals (get_prompt_responses, structure_system_prompt, the prompt
# constants, ...): monkeypatch string paths for those target
# voiceai.modules.voice.session.prompts.<name>.
from voiceai.modules.voice.session import prompts as _voice_prompts

# spec-0004 B7: the call-lifecycle bodies (__process_end_of_conversation, the dead
# __update_preprocessed_tree_node beside it, _enter_hangup_state /
# _should_ignore_transcriber_input / process_call_hangup, and the
# __check_for_completion / __check_for_backchanneling watchdogs) live VERBATIM in
# voiceai.modules.voice.session.lifecycle.hangup; the provider-health shadow
# (Region O) lives in voiceai.modules.voice.session.health. TaskManager keeps
# same-named thin delegators below and injects itself (the LifecycleSession /
# HealthSession facades) on every call (§3.1 bridge 3), and flag groups A+D now
# live on the CallLifecycle object behind forwarded_flag properties. The HANGUP
# module is now the lookup site for the moved bodies' globals
# (create_ws_data_packet, select_message_by_language, get_raw_audio_bytes,
# resample, wav_bytes_to_pcm, ...): monkeypatch string paths for those target
# voiceai.modules.voice.session.lifecycle.hangup.<name>.
from voiceai.modules.voice.session import health as _voice_health
from voiceai.modules.voice.session.lifecycle import hangup as _voice_hangup
from voiceai.modules.voice.session.lifecycle import report as _voice_report

# spec-0004 B8: the welcome bodies (__forced_first_message / __synthesize_welcome_audio /
# __first_message / handle_init_event), the DTMF queue consumer
# (inject_digits_to_conversation) and the proactive-event bodies (_listen_events /
# _wait_for_safe_point / _proactive_generate_for_event / _generate_proactive) live
# VERBATIM in voiceai.modules.voice.session.{welcome,dtmf,events}. TaskManager keeps
# same-named thin delegators below and injects itself (the WelcomeSession /
# DtmfSession / EventSession facades) on every call (§3.1 bridge 3). The WELCOME and
# EVENTS modules are now the lookup sites for the moved bodies' globals
# (create_ws_data_packet, convert_to_request_log, pcm_to_ulaw, calculate_audio_duration,
# wav_bytes_to_pcm, get_synth_audio_format, resample, update_prompt_with_context,
# get_md5_hash, select_message_by_language): monkeypatch string paths for those target
# voiceai.modules.voice.session.{welcome,events}.<name>.
from voiceai.modules.voice.session import chat as _voice_chat
from voiceai.modules.voice.session import dtmf as _voice_dtmf
from voiceai.modules.voice.session import events as _voice_events
from voiceai.modules.voice.session import welcome as _voice_welcome

# spec-0004 B9a: the language-subsystem bodies (Region Q — LID evidence + the playback
# gate + the idle watcher, the Switch-LLM decision path incl. the public
# handle_language_switch / switch_language, and the handoff clips) live VERBATIM in
# voiceai.modules.voice.session.language.{lid_gate,switcher,handoff}. TaskManager keeps
# a same-named thin delegator per moved name below (mangled _TaskManager__* spellings
# included) and injects itself (the LanguageSession facade) on every call (§3.1
# bridge 3). Those modules are now the lookup sites for the moved bodies' globals:
# monkeypatch string paths for those target
# voiceai.modules.voice.session.language.<module>.<name> (R3).
from voiceai.modules.voice.session.language import handoff as _voice_handoff
from voiceai.modules.voice.session.language import lid_gate as _voice_lid_gate
from voiceai.modules.voice.session.language import switcher as _voice_switcher

# spec-0004 B10: the history-commit bodies (sync_history and its evidence helpers,
# __cleanup_downstream_tasks and the speculation trio) live VERBATIM in
# voiceai.modules.voice.session.turn.history_sync; the InterruptionManager class
# lives in voiceai.modules.voice.session.interruption. TaskManager keeps
# same-named thin delegators below and injects itself (the HistorySession facade)
# on every call (§3.1 bridge 3). The HISTORY module is now the lookup site for
# those bodies' globals (convert_to_request_log, format_messages,
# NON_EVIDENCE_MARK_TYPES): monkeypatch string paths for those target
# voiceai.modules.voice.session.turn.history_sync.<name>.
from voiceai.modules.voice.session.turn import function_calls as _voice_function_calls
from voiceai.modules.voice.session.turn import generation as _voice_generation
from voiceai.modules.voice.session.turn import meta_info as _voice_meta_info
from voiceai.modules.voice.session.turn import output_loop as _voice_output_loop
from voiceai.modules.voice.session import composition as _voice_composition
from voiceai.modules.voice.session.turn import transcript_listener as _voice_listener
from voiceai.modules.voice.session.turn import history_sync as _voice_history
from voiceai.modules.voice.session import webhooks as _voice_webhooks

# spec-0004 B9a: the process-wide handoff clip cache moved WITH its owning subsystem
# (rule 1g; flagged at B3) into voiceai.modules.voice.session.language.handoff. These
# same-named bindings keep THIS module a lookup/import site by IDENTITY — the binding
# IS the one cache dict, so tests/test_handoff_prewarm.py's import-and-clear keeps
# operating on the real cache (the B3 welcome_pcm_upsampled precedent). The `as`
# spelling makes each binding an EXPLICIT re-export (no_implicit_reexport).
from voiceai.modules.voice.session.language.handoff import HANDOFF_CLIP_CACHE as HANDOFF_CLIP_CACHE
from voiceai.modules.voice.session.language.handoff import HANDOFF_CLIP_CACHE_MAX as HANDOFF_CLIP_CACHE_MAX

# spec-0004 B11c: the category set lives in voiceai.modules.voice.constants (rule 1b;
# the B9a HANDOFF_CLIP_CACHE precedent). This same-named binding keeps THIS module the
# lookup site BY IDENTITY for any reader still addressing it here.
from voiceai.modules.voice.constants import NON_NODE_RESPONSE_CATEGORIES as _NON_NODE_RESPONSE_CATEGORIES


class TaskManager(BaseManager):
    # Class-level default on purpose: __process_output_loop reads this for EVERY call (including
    # single-language ones), so an instance-only assignment that ever landed in a conditional
    # branch would raise AttributeError there — and that loop's handler sits outside its while,
    # so the loop would exit and the caller would hear nothing at all.
    lid_playback_gate = None

    def __init__(
        self,
        assistant_name,
        task_id,
        task,
        ws,
        input_parameters=None,
        context_data=None,
        assistant_id=None,
        turn_based_conversation=False,
        cache=None,
        input_queue=None,
        conversation_history=None,
        output_queue=None,
        yield_chunks=True,
        **kwargs,
    ):
        super().__init__()
        # spec-0004 B13a: the wiring lives VERBATIM in
        # voiceai.modules.voice.session.composition (Region D); this constructor keeps
        # its exact legacy dict signature (test_llm_verbosity_passthrough passes
        # UNMODIFIED) and only bundles its arguments for the composition root.
        _voice_composition.compose_call_session(
            self,
            _voice_composition.CallArgs(
                assistant_name=assistant_name,
                task_id=task_id,
                task=task,
                ws=ws,
                input_parameters=input_parameters,
                context_data=context_data,
                assistant_id=assistant_id,
                turn_based_conversation=turn_based_conversation,
                cache=cache,
                input_queue=input_queue,
                conversation_history=conversation_history,
                output_queue=output_queue,
                yield_chunks=yield_chunks,
                kwargs=kwargs,
            ),
        )

    @classmethod
    def from_components(cls, args):  # why: args is the CallArgs bundle by contract
        """Build a call session from an explicit ``CallArgs`` bundle (spec 0004, B13a).

        The post-cutover construction seam: same composition path as ``__init__``
        without the legacy positional signature. Harnesses keep using ``__new__``
        directly; this is the only other sanctioned entry.

        Args:
            args: The bundled constructor arguments (``CallArgs``).

        Returns:
            A fully composed live call session.
        """
        obj = cls.__new__(cls)
        super(TaskManager, obj).__init__()
        _voice_composition.compose_call_session(obj, args)
        return obj


    @staticmethod
    def _sanitize_api_call_headers(headers):
        return _voice_webhooks.sanitize_api_call_headers(headers)

    def _stamp_llm_latency_dict(
        self,
        latency_dict: dict,
        meta_info: dict,
        actual_input_tokens,
        actual_output_tokens,
        actual_reasoning_tokens,
        actual_cached_tokens,
        response_text: str | None = None,
    ) -> None:
        """Stamp observability fields onto an LLM turn latency dict.

        Moved verbatim to `voiceai.modules.voice.session.webhooks` (spec 0027);
        this delegator keeps legacy callers stable.
        """
        return _voice_webhooks.stamp_llm_latency_dict(
            self,
            latency_dict,
            meta_info,
            actual_input_tokens,
            actual_output_tokens,
            actual_reasoning_tokens,
            actual_cached_tokens,
            response_text,
        )

    @staticmethod
    def _extract_api_call_runtime_args(resp):
        return _voice_webhooks.extract_api_call_runtime_args(resp)

    def _build_call_context(self):
        return _voice_webhooks.build_call_context(self)

    def fire_pre_call_webhook(self, webhook_url, called_fun, resp, meta_info, webhook_param=None):
        """Fire-and-forget pre-call webhook before the tool's main request runs.

        Moved verbatim to `voiceai.modules.voice.session.webhooks` (spec 0027);
        this delegator keeps legacy callers stable.
        """
        return _voice_webhooks.fire_pre_call_webhook(self, webhook_url, called_fun, resp, meta_info, webhook_param)

    def _start_api_call_detail(
        self,
        *,
        called_fun,
        url,
        method,
        param,
        headers,
        meta_info,
        runtime_args,
        request_body=None,
        api_params=None,
    ):
        return _voice_webhooks.start_api_call_detail(
            self,
            called_fun=called_fun,
            url=url,
            method=method,
            param=param,
            headers=headers,
            meta_info=meta_info,
            runtime_args=runtime_args,
            request_body=request_body,
            api_params=api_params,
        )

    @staticmethod
    def _finalize_api_call_detail(api_call_detail, response=None, status_code=None, content_type=None, error=None):
        return _voice_webhooks.finalize_api_call_detail(
            api_call_detail, response=response, status_code=status_code, content_type=content_type, error=error
        )

    @property
    def history(self):
        return self.conversation_history.messages

    @history.setter
    def history(self, value):
        self.conversation_history._messages = value

    @property
    def interim_history(self):
        return self.conversation_history.interim

    @interim_history.setter
    def interim_history(self, value):
        self.conversation_history._interim = value

    @property
    def language(self) -> str:
        """Active language code.

        Returns the language-detector's result when detection has completed
        (non-multilingual path only: detection is disabled for multilingual
        pools, so dominant_language is always None). Falls back to the
        configured/switched language otherwise.
        """
        detector = getattr(self, "language_detector", None)
        if detector is not None:
            detected = detector.dominant_language
            if detected:
                return detected
        return self._language

    @language.setter
    def language(self, value: str):
        logger.info(f"Setting base language to {value}")
        self._language = value

    @property
    def call_hangup_message(self):
        return select_message_by_language(self.call_hangup_message_config, self.language)

    def __is_multiagent(self):
        return self.__agent_type() == "multiagent"

    def __agent_type(self):
        """Configured llm_agent type, or None for webhook and speech-to-speech tasks."""
        if self.task_config["task_type"] == "webhook":
            return None
        return (self.task_config["tools_config"].get("llm_agent") or {}).get("agent_type", None)

    def __is_knowledgebase_agent(self):
        return self.__agent_type() == "knowledgebase_agent"

    def __is_graph_agent(self):
        return self.__agent_type() == "graph_agent"

    def __is_s2s(self):
        return bool(self.s2s_config) and self._is_conversation_task()

    def _is_browser_leg(self) -> bool:
        """Playground/test socket leg: default IO handlers over the browser
        websocket, as opposed to a telephony carrier leg or dashboard
        turn-based session. s2s tasks are excluded — they consume the same
        llm queue in _s2s_text_loop, and two consumers would split-brain
        typed turns between them."""
        if self.turn_based_conversation or self.is_web_based_call:
            return False
        tools_config = (self.task_config or {}).get("tools_config", {}) or {}
        return (tools_config.get("input") or {}).get("provider") == "default"

    async def _forward_browser_text(self, text, role, asr_turn_id=None):
        """Forward one transcript line to the Live Talk / chat panel.

        Moved verbatim to `voiceai.modules.voice.session.chat` (spec 0032);
        this delegator keeps legacy callers stable.
        """
        return await _voice_chat.forward_browser_text(self, text, role, asr_turn_id)

    async def _drain_pending_chat_forward(self):
        """Flush staged agent replies to the browser transcript panel.

        Moved verbatim to `voiceai.modules.voice.session.chat` (spec 0032);
        this delegator keeps legacy callers stable.
        """
        return await _voice_chat.drain_pending_chat_forward(self)

    def _invalidate_response_chain(self):
        return _voice_history.invalidate_response_chain(self)

    def _set_interruption_hint(self, heard_text):
        return _voice_history.set_interruption_hint(self, heard_text)

    def _cancel_in_flight_llm_response(self):
        return _voice_history.cancel_in_flight_llm_response(self)

    def _inject_language_instruction(self, messages: list) -> list:
        """Inject language instruction into messages based on detected language.

        Moved verbatim to `voiceai.modules.voice.session.language.switcher`
        (spec 0034); this delegator keeps legacy callers stable.
        """
        return _voice_switcher.inject_language_instruction(self, messages)

    def __setup_output_handlers(self, turn_based_conversation, output_queue):
        output_kwargs = {"websocket": self.websocket}

        if self.task_config["tools_config"]["output"] is None:
            logger.info("Not setting up any output handler as it is none")
        elif self.task_config["tools_config"]["output"]["provider"] in SUPPORTED_OUTPUT_HANDLERS.keys():
            # Explicitly use default for turn based conversation as we expect to use HTTP endpoints
            if turn_based_conversation:
                logger.info("Connected through dashboard and hence using default output handler")
                output_handler_class = SUPPORTED_OUTPUT_HANDLERS.get("default")
            else:
                output_handler_class = SUPPORTED_OUTPUT_HANDLERS.get(
                    self.task_config["tools_config"]["output"]["provider"]
                )

                # A speech-to-speech agent has no synthesizer config to stamp; its run loop
                # encodes straight to the rate chosen here.
                is_s2s_output = self.__is_s2s()
                synth_config = None if is_s2s_output else self.task_config["tools_config"]["synthesizer"]
                if self.task_config["tools_config"]["output"]["provider"] in SUPPORTED_OUTPUT_TELEPHONY_HANDLERS.keys():
                    output_kwargs["mark_event_meta_data"] = self.mark_event_meta_data
                    logger.info(f"Making sure that the sampling rate for output handler is 8000")
                    if synth_config:
                        synth_config["provider_config"]["sampling_rate"] = 8000
                    # sip-trunk (Asterisk) uses ulaw; other telephony use pcm (handler converts to mulaw)
                    if self.task_config["tools_config"]["output"]["provider"] == TelephonyProvider.SIP_TRUNK.value:
                        if synth_config:
                            synth_config["audio_format"] = "ulaw"
                            logger.info(f"Setting synthesizer audio format to ulaw for Asterisk sip-trunk")
                        # Pass input handler to output handler so it can simulate mark events
                        input_handler = self.tools.get("input")
                        output_kwargs["input_handler"] = input_handler
                        output_kwargs["asterisk_media_start"] = (self.context_data or {}).get("media_start_data")
                        output_kwargs["agent_config"] = {"tasks": [self.task_config]}
                        logger.info(
                            f"Passing input_handler to sip-trunk output handler for mark event simulation: {input_handler is not None}"
                        )
                    elif synth_config:
                        synth_config["audio_format"] = "pcm"
                    self.sampling_rate = 8000
                else:
                    if synth_config:
                        synth_config["provider_config"]["sampling_rate"] = WEBCALL_TTS_SAMPLE_RATE
                    output_kwargs["queue"] = output_queue
                    self.sampling_rate = WEBCALL_TTS_SAMPLE_RATE

            if self.task_config["tools_config"]["output"]["provider"] == "default":
                output_kwargs["is_web_based_call"] = self.is_web_based_call
                output_kwargs["mark_event_meta_data"] = self.mark_event_meta_data
                output_kwargs["sampling_rate"] = self.sampling_rate

            # FreeSWITCH streams PCM @ self.sampling_rate; no mark echo → self-complete via
            # input_handler (input handler is set up before output, like sip-trunk).
            if self.task_config["tools_config"]["output"]["provider"] == TelephonyProvider.FREESWITCH.value:
                output_kwargs["mark_event_meta_data"] = self.mark_event_meta_data
                output_kwargs["sampling_rate"] = self.sampling_rate
                output_kwargs["input_handler"] = self.tools.get("input")

            self.tools["output"] = output_handler_class(**output_kwargs)
            self.output_handler_set = True
            logger.info("output handler set")
        else:
            # raising a plain string surfaces as TypeError("exceptions must derive from
            # BaseException") and hides which provider was unsupported
            raise ValueError(f"Unsupported output provider: {self.task_config['tools_config']['output']['provider']}")

    async def message_task_new(self):
        tasks = []
        if self._is_conversation_task():
            tasks.append(self.tools["input"].handle())

            if not self.turn_based_conversation and not self.is_web_based_call:
                # An s2s model speaks its own greeting, so it takes the stream id but not
                # the pre-rendered welcome audio it has no synthesizer to produce.
                if self.__is_s2s():
                    tasks.append(self._s2s_await_stream_sid())
                else:
                    tasks.append(self.__forced_first_message())

        if tasks:
            await asyncio.gather(*tasks)

    def __setup_input_handlers(self, turn_based_conversation, input_queue, should_record):
        if self.task_config["tools_config"]["input"]["provider"] in SUPPORTED_INPUT_HANDLERS.keys():
            input_kwargs = {
                "queues": self.queues,
                "websocket": self.websocket,
                "input_types": get_required_input_types(self.task_config),
                "mark_event_meta_data": self.mark_event_meta_data,
                "is_welcome_message_played": True
                if self.task_config["tools_config"]["output"]["provider"] == "default" and not self.is_web_based_call
                else False,
            }

            if should_record:
                input_kwargs["conversation_recording"] = self.conversation_recording

            if self.turn_based_conversation:
                input_kwargs["turn_based_conversation"] = True
                input_handler_class = SUPPORTED_INPUT_HANDLERS.get("default")
                input_kwargs["queue"] = input_queue
            else:
                input_handler_class = SUPPORTED_INPUT_HANDLERS.get(
                    self.task_config["tools_config"]["input"]["provider"]
                )

                if self.task_config["tools_config"]["input"]["provider"] == "default":
                    input_kwargs["queue"] = input_queue

                if self.task_config["tools_config"]["input"]["provider"] in (
                    TelephonyProvider.PLIVO.value,
                    TelephonyProvider.VOBIZ.value,
                ) and self.kwargs.get("telephony_credentials"):
                    input_kwargs["auth_credentials"] = self.kwargs["telephony_credentials"]

                input_kwargs["observable_variables"] = self.observable_variables

                # Asterisk (sip-trunk): pass context data for pre-parsed MEDIA_START
                if (
                    self.task_config["tools_config"]["input"]["provider"] == TelephonyProvider.SIP_TRUNK.value
                    and self.context_data
                ):
                    input_kwargs["ws_context_data"] = self.context_data
                    input_kwargs["agent_config"] = {"tasks": [self.task_config]}
            self.tools["input"] = input_handler_class(**input_kwargs)
        else:
            # raising a plain string surfaces as TypeError("exceptions must derive from
            # BaseException") and hides which provider was unsupported — this exact failure
            # masked the missing-freeswitch-handler case when a PyPI voiceai shadowed the branch
            raise ValueError(f"Unsupported input provider: {self.task_config['tools_config']['input']['provider']}")

    async def __await_stream_sid(self, timeout=10.0):
        """Wait for the carrier's stream id and hand it to the output handler.

        Nothing reaches the caller until the output handler holds this: it drops every
        packet while stream_sid is None. Returns whether the id arrived in time.
        """
        # output_handler_set is not part of the wait: __setup_output_handlers runs in __init__,
        # so it is already true by the time this task exists.
        logger.info("Waiting for stream_sid before sending the welcome message")
        try:
            await asyncio.wait_for(self.tools["input"].stream_sid_ready.wait(), timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout reached while waiting for stream_sid after {timeout}s")
            await self.__process_end_of_conversation()
            return False

        self.stream_sid_ts = time.time() * 1000
        await self._report_stream_connect()
        self.stream_sid = self.tools["input"].get_stream_sid()
        await self.tools["output"].set_stream_sid(self.stream_sid)
        return True

    async def _s2s_await_stream_sid(self):
        """Claim the stream id for an s2s call, which has no welcome audio to play.

        The model speaks its own greeting, so the welcome path below is skipped, but it
        was also the only thing propagating the stream id, and without it the output
        handler silently discards the whole conversation.
        """
        try:
            if await self.__await_stream_sid():
                logger.info(f"Got stream sid for s2s conversation {self.stream_sid}")
                self.tools["input"].set_welcome_message_played(True)
                self._s2s_stream_ready.set()
        except Exception as e:
            logger.error(f"Exception in _s2s_await_stream_sid {str(e)}")

    # spec-0004 B8: the welcome bodies (__forced_first_message / __synthesize_welcome_audio
    # here; __first_message / handle_init_event at their original sites below) live
    # VERBATIM in voiceai.modules.voice.session.welcome (see the import block above).
    # Each same-named thin delegator keeps this class the resolution site
    # (instance-attr AsyncMock overrides, __new__ harnesses and internal self-dispatch
    # via the mangled _TaskManager__* spellings) and injects the session (self, the
    # WelcomeSession facade) into the welcome module (§3.1 bridge 3). A delegator is
    # deleted only in the commit that ports its pinning tests (spec 0004 iron rule).
    async def __forced_first_message(self, timeout=10.0):
        return await _voice_welcome.forced_first_message(self, timeout=timeout)

    async def __synthesize_welcome_audio(self, text):
        return await _voice_welcome.synthesize_welcome_audio(self, text)

    def __inject_switch_language_tool(self):
        """Auto-inject the switch_language tool when multilingual pools are active.

        Moved verbatim to `voiceai.modules.voice.session.language.switcher`
        (spec 0031); this delegator keeps legacy callers (including the
        spec-0004 B9b pin) stable.
        """
        return _voice_switcher.inject_switch_language_tool(self)

    def _get_voice_name_for_label(self, label):
        """Get agent name for a language label from configured agent_names."""
        return self.agent_names.get(label, "")

    def __setup_transcriber(self):
        try:
            if self.task_config["tools_config"]["transcriber"] is not None:
                transcriber_config = self.task_config["tools_config"]["transcriber"]

                self.transcriber_provider = transcriber_config.get("provider", transcriber_config.get("model"))

                # --- Multilingual pool path ---
                if "multilingual" in transcriber_config and transcriber_config.get("multilingual"):
                    multilingual = transcriber_config["multilingual"]
                    active_label = transcriber_config.get("active", DEFAULT_LANGUAGE_CODE)
                    self.language = active_label
                    if hasattr(self, "language_detector"):
                        self.language_detector.set_enabled_status(
                            False
                        )  # Disable language detection when using multilingual pool

                    if self.turn_based_conversation:
                        provider = "playground"
                    elif self.is_web_based_call:
                        provider = "web_based_call"
                    else:
                        provider = self.task_config["tools_config"]["input"]["provider"]

                    is_sip = provider == TelephonyProvider.SIP_TRUNK.value

                    transcribers = {}
                    for label, cfg in multilingual.items():
                        private_queue = asyncio.Queue()
                        cfg["input_queue"] = private_queue
                        cfg["output_queue"] = self.transcriber_output_queue
                        if is_sip:
                            cfg["encoding"] = "mulaw"
                            cfg["sampling_rate"] = 8000
                        elif provider in (WEB_BASED_CALL_PROVIDER, TelephonyProvider.FREESWITCH.value):
                            cfg["encoding"] = "linear16"
                            cfg["sampling_rate"] = 16000
                        if self.turn_based_conversation:
                            cfg["stream"] = True if self.enforce_streaming else False

                        if "provider" in cfg:
                            cls = SUPPORTED_TRANSCRIBER_PROVIDERS.get(cfg["provider"])
                        else:
                            cls = SUPPORTED_TRANSCRIBER_MODELS.get(cfg["model"])
                        transcribers[label] = cls(provider, **cfg, **self.kwargs)

                        if label == active_label:
                            self.transcriber_provider = cfg.get("provider", cfg.get("model"))

                    # Detector tap: enabled → per-turn buffer feeds the Switch LLM; disabled → legacy heuristic.
                    LID_PROVIDER = self.task_config.get("tools_config", {}).get(
                        "language_switch_lid_provider"
                    ) or os.getenv("LID_PROVIDER", "sarvam")
                    lid_config = {"telephony_provider": provider}
                    # Sarvam-only model override (language_switch_saaras_v4_lid flag).
                    lid_model = self.task_config.get("tools_config", {}).get("language_switch_lid_model")
                    if lid_model:
                        lid_config["sarvam_model"] = lid_model
                    switch_enabled = self.__language_switch_enabled()
                    if switch_enabled:
                        # language_switch_llm (from the azure flag) overrides the model; absent → default.
                        self.language_switcher = LanguageSwitcher(
                            available_labels=list(transcribers.keys()),
                            run_id=self.run_id,
                            model=self.task_config.get("tools_config", {}).get("language_switch_llm"),
                            # Per-agent toggle: judge switches only on an explicit request/selection.
                            explicit_only=bool(
                                self.task_config.get("tools_config", {}).get("language_switch_explicit_only")
                            ),
                        )
                        self.language_switcher.prewarm()  # pay the TLS handshake now

                    self.tools["transcriber"] = TranscriberPool(
                        transcribers=transcribers,
                        shared_input_queue=self.audio_queue,
                        output_queue=self.transcriber_output_queue,
                        active_label=active_label,
                        multilingual_config=multilingual,
                        lid_provider=LID_PROVIDER,
                        lid_config=lid_config,
                        on_lid_switch=None if switch_enabled else self.switch_language,
                    )
                    logger.info(
                        f"TranscriberPool created with labels={list(transcribers.keys())}, "
                        f"active='{active_label}', language_switch_enabled={switch_enabled}, "
                        f"lid_provider={LID_PROVIDER!r}, lid_model={lid_model or 'default'}, "
                        f"legacy_lid_heuristic={not switch_enabled}"
                    )
                    if switch_enabled:
                        # Idle-flush fallback: recovers the stuck-language deadlock where
                        # the locked ASR can't decode the caller's speech, so no main turn
                        # ever fires and the switch decision would otherwise never run.
                        self._lid_idle_watcher_task = asyncio.create_task(self.__lid_idle_watcher())
                    return

                # --- Single transcriber path (unchanged) ---
                self.language = transcriber_config.get("language", DEFAULT_LANGUAGE_CODE)
                if self.turn_based_conversation:
                    provider = "playground"
                elif self.is_web_based_call:
                    provider = "web_based_call"
                else:
                    provider = self.task_config["tools_config"]["input"]["provider"]

                transcriber_config["input_queue"] = self.audio_queue
                transcriber_config["output_queue"] = self.transcriber_output_queue

                # Configure encoding for Asterisk/sip-trunk (uses ulaw like Twilio)
                if provider == TelephonyProvider.SIP_TRUNK.value:
                    transcriber_config["encoding"] = "mulaw"
                    transcriber_config["sampling_rate"] = 8000
                    logger.info(f"Configured transcriber for Asterisk sip-trunk with mulaw encoding @ 8kHz")
                elif provider in (WEB_BASED_CALL_PROVIDER, TelephonyProvider.FREESWITCH.value):
                    # Web + FreeSWITCH fork both stream linear16 PCM @16kHz; coerce for all ASR providers.
                    transcriber_config["encoding"] = "linear16"
                    transcriber_config["sampling_rate"] = 16000

                # Checking models for backwards compatibility
                if (
                    transcriber_config["model"] in SUPPORTED_TRANSCRIBER_MODELS.keys()
                    or transcriber_config["provider"] in SUPPORTED_TRANSCRIBER_PROVIDERS.keys()
                ):
                    if self.turn_based_conversation:
                        transcriber_config["stream"] = True if self.enforce_streaming else False
                        logger.info(
                            f"transcriber stream={transcriber_config['stream']} enforce_streaming={self.enforce_streaming}"
                        )
                    if "provider" in transcriber_config:
                        transcriber_class = SUPPORTED_TRANSCRIBER_PROVIDERS.get(transcriber_config["provider"])
                    else:
                        transcriber_class = SUPPORTED_TRANSCRIBER_MODELS.get(transcriber_config["model"])
                    self.tools["transcriber"] = transcriber_class(provider, **transcriber_config, **self.kwargs)
        except Exception as e:
            logger.error(f"Something went wrong with starting transcriber {e}")

    def __setup_synthesizer(self, llm_config=None):
        if self._is_conversation_task():
            # Text agents carry no transcriber block; default the flag instead
            # of crashing on ["language"] (this bug killed every live text run).
            transcriber_cfg = (self.task_config.get("tools_config", {}) or {}).get("transcriber") or {}
            self.kwargs["use_turbo"] = transcriber_cfg.get("language") == DEFAULT_LANGUAGE_CODE
        if self.task_config["tools_config"]["synthesizer"] is not None:
            synth_config = self.task_config["tools_config"]["synthesizer"]

            # --- Multilingual pool path ---
            if "multilingual" in synth_config:
                multilingual = synth_config["multilingual"]
                active_label = synth_config.get("active", DEFAULT_LANGUAGE_CODE)
                if hasattr(self, "language_detector"):
                    self.language_detector.set_enabled_status(
                        False
                    )  # Disable language detection when using multilingual pool

                # Telephony providers expect mulaw@8000Hz — force use_mulaw for all synths in the pool
                output_provider = self.task_config["tools_config"]["output"]["provider"]
                is_telephony = output_provider in SUPPORTED_OUTPUT_TELEPHONY_HANDLERS
                synthesizer_kwargs = self.kwargs.copy()
                if is_telephony:
                    synthesizer_kwargs["use_mulaw"] = True
                elif self.is_web_based_call or output_provider == TelephonyProvider.FREESWITCH.value:
                    # web/freeswitch play raw PCM @24k; synths like elevenlabs/cartesia default to
                    # mulaw@8k (telephony) which garbles when labeled 24k — force it off.
                    synthesizer_kwargs["use_mulaw"] = False

                synthesizers = {}
                for label, cfg in multilingual.items():
                    cfg = dict(cfg)  # shallow copy so pops don't mutate original
                    caching = cfg.pop("caching", True)
                    provider_name = cfg.pop("provider")
                    provider_config = cfg.pop("provider_config")

                    # Web + FreeSWITCH play raw PCM at a fixed 24kHz; force every language synth to
                    # match (telephony/chat untouched). Else non-24k languages drift (e.g. Hindi too slow).
                    if self.is_web_based_call or (
                        self.task_config["tools_config"]["output"]["provider"] == TelephonyProvider.FREESWITCH.value
                    ):
                        provider_config = dict(provider_config)  # don't mutate the cached agent config
                        provider_config["sampling_rate"] = WEBCALL_TTS_SAMPLE_RATE
                        cfg.pop("sampling_rate", None)  # avoid passing sampling_rate twice to the synth

                    if self.turn_based_conversation:
                        cfg["audio_format"] = "mp3"
                        cfg["stream"] = True if self.enforce_streaming else False

                    cls = SUPPORTED_SYNTHESIZER_MODELS.get(provider_name)
                    synthesizers[label] = cls(**cfg, **provider_config, **synthesizer_kwargs, caching=caching)

                # Use active synth's provider/voice for logging metadata, and buffer_size
                # Note that in the current state, buffer_size of other synth configs is ignored
                active_cfg = synth_config
                if active_label in multilingual:
                    active_cfg = multilingual[active_label]

                self.synthesizer_provider = active_cfg.get("provider", "unknown")
                self.synthesizer_voice = active_cfg.get("provider_config", {}).get("voice", "unknown")

                self.tools["synthesizer"] = SynthesizerPool(
                    synthesizers=synthesizers, active_label=active_label, multilingual_config=multilingual
                )

                logger.info(f"SynthesizerPool created with labels={list(synthesizers.keys())}, active='{active_label}'")

                # Pre-render every language's handoff clip on its own voice (background;
                if self.language_switcher is not None and not self.turn_based_conversation:
                    # "default" has one wire format only on the web path.
                    if self.task_config["tools_config"]["output"]["provider"] != "default" or self.is_web_based_call:
                        self.handoff_prewarm_task = asyncio.create_task(self.__prewarm_handoff_clips())

                if self.task_config["tools_config"]["llm_agent"] is not None and llm_config is not None:
                    llm_config["buffer_size"] = active_cfg.get("buffer_size")
                return

            # --- Single synthesizer path (apna normal path) ---
            if "caching" in synth_config:
                caching = synth_config.pop("caching")
            else:
                caching = True

            self.synthesizer_provider = synth_config.pop("provider")
            synthesizer_class = SUPPORTED_SYNTHESIZER_MODELS.get(self.synthesizer_provider)
            provider_config = synth_config.pop("provider_config")
            self.synthesizer_voice = provider_config["voice"]
            self.synthesizer_voice_id = provider_config.get("voice_id")
            self.synthesizer_model = provider_config.get("model")
            if self.turn_based_conversation:
                synth_config["audio_format"] = "mp3"  # Hard code mp3 if we're connected through dashboard
                synth_config["stream"] = (
                    True if self.enforce_streaming else False
                )  # Hardcode stream to be False as we don't want to get blocked by a __listen_synthesizer co-routine

            # Telephony providers expect mulaw@8000Hz — force use_mulaw regardless of the
            # server-side kwarg (it defaults to False and only covers some synths), mirroring
            # the multilingual-pool path above. Synths that honor the kwarg (e.g. cartesia)
            # would otherwise stream raw PCM that telephony plays as mulaw → loud static.
            output_provider = self.task_config["tools_config"]["output"]["provider"]
            is_telephony = output_provider in SUPPORTED_OUTPUT_TELEPHONY_HANDLERS
            synthesizer_kwargs = self.kwargs.copy()
            if is_telephony:
                synthesizer_kwargs["use_mulaw"] = True
            elif self.is_web_based_call or output_provider == TelephonyProvider.FREESWITCH.value:
                # web/freeswitch play raw PCM @24k — synths must not emit telephony mulaw@8k
                synthesizer_kwargs["use_mulaw"] = False

            if self.synthesizer_provider == "sarvam" and isinstance(provider_config, dict):
                # SarvamConfig drops sampling_rate, so the synth always ran at its 8000
                # default: right for telephony, but browser audio played 8k content at
                # 24k (chipmunk/beeps). Mirror the multilingual-pool stamping above.
                provider_config.setdefault(
                    "sampling_rate",
                    8000 if output_provider in SUPPORTED_OUTPUT_TELEPHONY_HANDLERS else WEBCALL_TTS_SAMPLE_RATE,
                )

            self.tools["synthesizer"] = synthesizer_class(
                **synth_config, **provider_config, **synthesizer_kwargs, caching=caching
            )
            # if not self.turn_based_conversation:
            #     self.synthesizer_monitor_task = asyncio.create_task(self.tools['synthesizer'].monitor_connection())
            if self.task_config["tools_config"]["llm_agent"] is not None and llm_config is not None:
                llm_config["buffer_size"] = synth_config.get("buffer_size")

    def __setup_llm(self, llm_config, task_id=0):
        if self.task_config["tools_config"]["llm_agent"] is not None:
            if task_id and task_id > 0:
                self.kwargs.pop("llm_key", None)
                self.kwargs.pop("base_url", None)
                self.kwargs.pop("api_version", None)

                if self._is_summarization_task() or self._is_extraction_task():
                    llm_config["model"] = LLM_DEFAULT_CONFIGS["summarization"]["model"]
                    llm_config["provider"] = LLM_DEFAULT_CONFIGS["summarization"]["provider"]

            if llm_config["provider"] in SUPPORTED_LLM_PROVIDERS.keys():
                llm_class = SUPPORTED_LLM_PROVIDERS.get(llm_config["provider"])
                llm = llm_class(language=self.language, **llm_config, **self.kwargs)
                return llm
            else:
                raise Exception(f"LLM {llm_config['provider']} not supported")

    def __get_agent_object(self, llm, agent_type, assistant_config=None):
        self.agent_type = agent_type
        # Spec 0024 (M3 cutover): conversation brains build ONLY through the
        # injected BrainFactory (prod, via the `brain_factory` task kwarg from
        # adapters/manager). Receiving an injected object is not an import
        # (AGENTS.md §3.1 bridge 3), so this legacy file gains no imports.
        # The verbatim graph/knowledgebase branches deleted here were proven
        # identical by tests/test_brain_factory_equivalence.py before removal.
        factory = self.kwargs.get("brain_factory")
        if factory is None:
            raise RuntimeError(
                "TaskManager requires a 'brain_factory' task kwarg (spec 0024 M3): "
                "pass BrainFactory() from voiceai.modules.agents."
            )
        return factory.build(agent_type, llm, self)

    def __setup_s2s(self):
        """Validate the S2S config. The provider itself is built once prompts are loaded."""
        self.s2s = S2SConfig(**self.s2s_config)
        self.s2s_provider_name = self.s2s.provider
        self.s2s_model = self.s2s.provider_config.model
        # Not in _run_s2s_conversation: message_task_new sets this and is scheduled first.
        self._s2s_stream_ready = asyncio.Event()
        logger.info(f"S2S agent configured | provider={self.s2s_provider_name} model={self.s2s_model}")

    def __setup_tasks(self, llm=None, agent_type=None, assistant_config=None):
        if self.task_config["task_type"] == "conversation" and not self.__is_multiagent():
            self.tools["llm_agent"] = self.__get_agent_object(llm, agent_type, assistant_config)
        elif self.__is_multiagent():
            return self.__get_agent_object(llm, agent_type, assistant_config)
        elif self.task_config["task_type"] == "extraction":
            logger.info("Setting up extraction agent")
            self.tools["llm_agent"] = ExtractionContextualAgent(llm, prompt=self.system_prompt)
            self.extracted_data = None
        elif self.task_config["task_type"] == "summarization":
            logger.info("Setting up summarization agent")
            self.tools["llm_agent"] = SummarizationContextualAgent(llm, prompt=self.system_prompt)
            self.summarized_data = None
        logger.info("prompt and config setup completed")

    ########################
    # Helper methods
    ########################

    # spec-0004 B6: the runtime prompt-loading bodies (Region E) live VERBATIM in
    # voiceai.modules.voice.session.prompts. Each same-named thin delegator below
    # keeps this class the resolution site for the assistant_manager fan-out,
    # patch.object, __new__ harnesses and internal self-dispatch (the mangled
    # _TaskManager__* spellings keep resolving), and injects the session (self, the
    # PromptSession facade) into the prompts module (§3.1 bridge 3). A delegator is
    # deleted only in the commit that ports its pinning tests (spec 0004 iron rule).

    def __get_final_prompt(self, prompt, today, current_time, current_timezone):
        return _voice_prompts.get_final_prompt(self, prompt, today, current_time, current_timezone)

    async def load_prompt(self, assistant_name, task_id, local, **kwargs):
        return await _voice_prompts.load_prompt(self, assistant_name, task_id, local, **kwargs)

    def __prefill_prompts(self, task, prompt, task_type):
        return _voice_prompts.prefill_prompts(self, task, prompt, task_type)

    def __process_stop_words(self, text_chunk, meta_info):
        return _voice_prompts.process_stop_words(self, text_chunk, meta_info)

    def update_transcript_for_interruption(self, original_stream, heard_text):
        return _voice_history.update_transcript_for_interruption(self, original_stream, heard_text)

    # spec-0004 B10: pure evidence readers live in history_sync; these bindings keep
    # THIS module the lookup site BY IDENTITY (the B3 welcome_pcm_upsampled / B9a
    # pure-reader precedent), so unbound TaskManager._get_latest_*(marks) calls keep
    # resolving without a delegator hop.
    _trim_partial_to_complete_words = staticmethod(
        _voice_history.trim_partial_to_complete_words
    )
    _normalized_transcript_text = staticmethod(_voice_history.normalized_transcript_text)
    _prepare_precise_transcript_messages = staticmethod(
        _voice_history.prepare_precise_transcript_messages
    )
    _get_latest_turn_id_from_marks = staticmethod(_voice_history.get_latest_turn_id_from_marks)
    _get_latest_response_uid_from_marks = staticmethod(
        _voice_history.get_latest_response_uid_from_marks
    )

    def _get_latest_assistant_turn_id(self):
        return _voice_history.get_latest_assistant_turn_id(self)

    def _get_latest_assistant_response_uid(self):
        return _voice_history.get_latest_assistant_response_uid(self)
        return None

    def _has_interruptible_mark_activity(self):
        return _voice_history.has_interruptible_mark_activity(self)

    def _inflight_response_activity(self, exclude_sequence_id=None) -> dict:
        return _voice_history.inflight_response_activity(self, exclude_sequence_id)

    def estimate_played_text_for_time(self, pending_chunks, actual_play_time):
        return _voice_history.estimate_played_text_for_time(self, pending_chunks, actual_play_time)

    async def sync_history(self, mark_events_data, interruption_processed_at, extend_with_playback_estimate=False):
        return await _voice_history.sync_history(self, mark_events_data, interruption_processed_at, extend_with_playback_estimate)

    async def __cleanup_downstream_tasks(self):
        return await _voice_history.cleanup_downstream_tasks(self)

    def __get_updated_meta_info(self, meta_info=None):
        """Stamp a fresh response identity onto copied meta info.

        Moved verbatim to `voiceai.modules.voice.session.turn.meta_info`
        (spec 0033); this delegator keeps legacy callers (including mangled
        `_TaskManager__get_updated_meta_info` dispatch) stable.
        """
        return _voice_meta_info.get_updated_meta_info(self, meta_info)

    def _spawn_followup_meta_info(self, meta_info):
        """Derive a followup response identity linked to its parent.

        Moved verbatim to `voiceai.modules.voice.session.turn.meta_info`
        (spec 0033); this delegator keeps legacy callers stable.
        """
        return _voice_meta_info.spawn_followup_meta_info(self, meta_info)

    def _extract_sequence_and_meta(self, message):
        return _voice_listener.extract_sequence_and_meta(self, message)

    def _is_extraction_task(self):
        return _voice_listener.is_extraction_task(self)

    def _is_summarization_task(self):
        return _voice_listener.is_summarization_task(self)

    def _is_conversation_task(self):
        return _voice_listener.is_conversation_task(self)

    def _get_next_step(self, sequence, origin):
        return _voice_listener.get_next_step(self, sequence, origin)

    def _set_call_details(self, message):
        return _voice_listener.set_call_details(self, message)

    async def _process_followup_task(self, message=None):
        return await _voice_listener.process_followup_task(self, message)

    def final_chunk_played_observer(self, is_final_chunk_played):
        return _voice_output_loop.final_chunk_played_observer(self, is_final_chunk_played)

    async def agent_hangup_observer(self, is_agent_hangup):
        return await _voice_output_loop.agent_hangup_observer(self, is_agent_hangup)

    async def wait_for_current_message(self):
        try:
            await asyncio.wait_for(self._turn_audio_flushed.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("wait_for_current_message: synth pipeline flush timed out after 3s")

        entry_time = time.time()
        while not self.conversation_ended:
            mark_events = self.mark_event_meta_data.mark_event_meta_data
            mark_items_list = [{"mark_id": k, "mark_data": v} for k, v in mark_events.items()]
            logger.info(f"current_list: {mark_items_list}")

            if not mark_items_list:
                break

            first_item = mark_items_list[0]["mark_data"]
            if len(mark_items_list) == 1 and first_item.get("type") == "pre_mark_message":
                break

            # plivo mark_event bug
            if len(mark_items_list) == 2:
                second_item = mark_items_list[1]["mark_data"]
                if (
                    first_item.get("type") == "agent_hangup"
                    and first_item.get("text_synthesized") == ""
                    and second_item.get("type") == "pre_mark_message"
                ):
                    break

            if first_item.get("text_synthesized") and first_item.get("is_final_chunk") is True:
                break

            # Use entry_time (not time.time()) so the deadline is a fixed point in the
            # future rather than one that recedes with each iteration. Without this,
            # `remaining = sum(durations) + hangup_mark_event_timeout` never reaches 0
            # when Plivo stops ACKing marks, causing an indefinite spin.
            remaining_durations = [
                v.get("duration", 0)
                for v in mark_events.values()
                if v.get("type") != "pre_mark_message" and v.get("sent_ts")
            ]
            expected_play_end = (entry_time + sum(remaining_durations)) if remaining_durations else entry_time
            deadline = expected_play_end + self.hangup_mark_event_timeout

            remaining = deadline - time.time()
            if remaining <= 0:
                logger.warning(
                    f"wait_for_current_message timed out: {len(mark_events)} marks unflushed, "
                    f"expected_play_end was {expected_play_end - entry_time:.1f}s after entry, "
                    f"grace {self.hangup_mark_event_timeout}s exceeded"
                )
                break

            self.mark_event_meta_data.mark_changed.clear()
            try:
                await asyncio.wait_for(self.mark_event_meta_data.mark_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
        return

    # spec-0004 B8: the DTMF consumer body lives VERBATIM in
    # voiceai.modules.voice.session.dtmf and the proactive-event bodies
    # (_listen_events / _wait_for_safe_point / _proactive_generate_for_event /
    # _generate_proactive) in voiceai.modules.voice.session.events. Each same-named
    # thin delegator keeps this class the resolution site (instance-attr AsyncMock
    # overrides, __new__ harnesses and internal self-dispatch) and injects the
    # session (self, the DtmfSession / EventSession facades) on every call (§3.1
    # bridge 3). The tm:697 single-consumer guard on the dtmf queue stays at its
    # __init__ call site above (`dtmf_enabled and not self.__is_s2s()`). The EVENTS
    # module is now the lookup site for the event bodies' globals
    # (create_ws_data_packet, get_md5_hash, select_message_by_language,
    # update_prompt_with_context): monkeypatch string paths for those target
    # voiceai.modules.voice.session.events.<name>. A delegator is deleted only in
    # the commit that ports its pinning tests (spec 0004 iron rule).
    async def inject_digits_to_conversation(self) -> None:
        return await _voice_dtmf.inject_digits_to_conversation(self)

    async def _listen_events(self):
        return await _voice_events.listen_events(self)

    async def _wait_for_safe_point(self, timeout=30.0):
        return await _voice_events.wait_for_safe_point(self, timeout=timeout)

    async def _proactive_generate_for_event(self, event: dict, result: dict):
        return await _voice_events.proactive_generate_for_event(self, event, result)

    async def _generate_proactive(self):
        return await _voice_events.generate_proactive(self)

    # spec-0004 B7: the call-lifecycle bodies (__process_end_of_conversation and the dead
    # __update_preprocessed_tree_node here; _enter_hangup_state /
    # _should_ignore_transcriber_input / process_call_hangup and the
    # __check_for_completion / __check_for_backchanneling watchdogs at their original
    # sites below) live VERBATIM in voiceai.modules.voice.session.lifecycle.hangup.
    # Each same-named thin delegator keeps this class the resolution site
    # (patch.object, __new__ harnesses, __get__-rebinds and internal self-dispatch via
    # the mangled _TaskManager__* spellings) and injects the session (self, the
    # LifecycleSession facade) into the hangup module (§3.1 bridge 3). Flag groups A
    # (hangup actuation) and D (teardown) now LIVE on the CallLifecycle object: the
    # forwarded_flag properties below keep every `self.<flag>` read/write — __init__'s
    # seeding above, run()'s goodbye-drain gate, and the Category-C harnesses that
    # hand-set hangup_triggered/_end_call_in_progress on bare __new__ instances —
    # flowing through the object, which is created LAZILY on first touch. A
    # delegator/property is deleted only in the commit that ports its pinning tests
    # (spec 0004 iron rule).

    @property
    def _call_lifecycle(self):
        """The session's CallLifecycle state holder (lazily created; spec 0004 B7)."""
        return _voice_hangup.session_lifecycle(self)

    hangup_triggered = _voice_hangup.forwarded_flag("hangup_triggered")
    hangup_triggered_at = _voice_hangup.forwarded_flag("hangup_triggered_at")
    hangup_decision_at = _voice_hangup.forwarded_flag("hangup_decision_at")
    _hangup_processing = _voice_hangup.forwarded_flag("_hangup_processing")
    hangup_message_queued = _voice_hangup.forwarded_flag("hangup_message_queued")
    conversation_ended = _voice_hangup.forwarded_flag("conversation_ended")
    _end_of_conversation_in_progress = _voice_hangup.forwarded_flag("_end_of_conversation_in_progress")
    _end_call_in_progress = _voice_hangup.forwarded_flag("_end_call_in_progress")
    ended_by_assistant = _voice_hangup.forwarded_flag("ended_by_assistant")

    async def __process_end_of_conversation(self, web_call_timeout=False):
        return await _voice_hangup.process_end_of_conversation(self, web_call_timeout=web_call_timeout)

    async def drain_hangup_goodbye(self):
        return await _voice_hangup.drain_hangup_goodbye(self)

    def __update_preprocessed_tree_node(self):
        return _voice_hangup.update_preprocessed_tree_node(self)

    ##############################################################
    # LLM task
    ##############################################################
    async def _handle_llm_output(
        self, next_step, text_chunk, should_bypass_synth, meta_info, is_filler=False, is_function_call=False
    ):
        return await _voice_generation.handle_llm_output(
            self, next_step, text_chunk, should_bypass_synth, meta_info, is_filler, is_function_call
        )

    async def _process_conversation_preprocessed_task(self, message, sequence, meta_info):
        return await _voice_generation.process_conversation_preprocessed_task(self, message, sequence, meta_info)

    async def _process_conversation_formulaic_task(self, message, sequence, meta_info):
        return await _voice_generation.process_conversation_formulaic_task(self, message, sequence, meta_info)

    async def __execute_function_call(
        self, url, method, param, api_token, headers, model_args, meta_info, next_step, called_fun, **resp
    ):
        return await _voice_function_calls.execute_function_call(
            self, url, method, param, api_token, headers, model_args, meta_info, next_step, called_fun, **resp
        )

    def __store_into_history(
        self,
        meta_info,
        messages,
        llm_response,
        should_trigger_function_call=False,
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        cached_tokens=None,
        reasoning_content=None,
        log_message=None,
        overflowed=False,
    ):
        return _voice_generation.store_into_history(
            self,
            meta_info,
            messages,
            llm_response,
            should_trigger_function_call,
            input_tokens,
            output_tokens,
            reasoning_tokens,
            cached_tokens,
            reasoning_content,
            log_message,
            overflowed,
        )

    async def _llm_stream_with_first_chunk_timeout(
        self, stream: object, meta_info: dict, timeout_s: float | None = None
    ) -> object:
        async for item in _voice_generation.llm_stream_with_first_chunk_timeout(self, stream, meta_info, timeout_s):
            yield item

    async def __do_llm_generation(
        self, messages, meta_info, next_step, should_bypass_synth=False, should_trigger_function_call=False
    ):
        return await _voice_generation.do_llm_generation(
            self, messages, meta_info, next_step, should_bypass_synth, should_trigger_function_call
        )

    def _append_eager_llm_stub(self, meta_info):
        return _voice_generation.append_eager_llm_stub(self, meta_info)

    async def _process_conversation_task(self, message, sequence, meta_info):
        return await _voice_generation.process_conversation_task(self, message, sequence, meta_info)

    def _enter_hangup_state(self):
        return _voice_hangup.enter_hangup_state(self)

    def _should_ignore_transcriber_input(self) -> bool:
        return _voice_listener.should_ignore_transcriber_input(self)

    async def process_call_hangup(self):
        return await _voice_hangup.process_call_hangup(self)

    async def _execute_transfer_call_webhook(self, called_fun, url, param, resp, meta_info):
        return await _voice_function_calls.execute_transfer_call_webhook(
            self, called_fun, url, param, resp, meta_info
        )

    async def _listen_llm_input_queue(self):
        return await _voice_listener.listen_llm_input_queue(self)

    async def _run_llm_task(self, message):
        return await _voice_listener.run_llm_task(self, message)

    async def process_transcriber_request(self, meta_info):
        return await _voice_listener.process_transcriber_request(self, meta_info)

    def _trigger_voicemail_check(self, transcriber_message, meta_info, is_final=True):
        return _voice_listener.trigger_voicemail_check(self, transcriber_message, meta_info, is_final)

    def _stage_assistant_history(self, meta_info, content):
        return _voice_history.stage_assistant_history(self, meta_info, content)

    def _commit_staged_assistant_history(self, sequence_id):
        return _voice_history.commit_staged_assistant_history(self, sequence_id)

    def _drop_staged_assistant_history(self, sequence_id, reason):
        return _voice_history.drop_staged_assistant_history(self, sequence_id, reason)

    def _drop_all_staged_assistant_history(self, reason, keep_sequence_ids=None):
        return _voice_listener.drop_all_staged_assistant_history(self, reason, keep_sequence_ids)

    def _retire_dropped_response(self, meta_info, reason):
        return _voice_listener.retire_dropped_response(self, meta_info, reason)

    def kickoff_llm_generation(self, transcriber_message, meta_info):
        return _voice_listener.kickoff_llm_generation(self, transcriber_message, meta_info)

    def regen_settle_armed(self) -> bool:
        return _voice_listener.regen_settle_armed(self)

    def regen_settle_can_fire(self):
        return _voice_listener.regen_settle_can_fire(self)

    def arm_regen_settle(self, transcriber_message, meta_info):
        return _voice_listener.arm_regen_settle(self, transcriber_message, meta_info)

    async def __regen_after_settle(self):
        return await _voice_listener.regen_after_settle(self)

    async def _handle_transcriber_output(self, next_task, transcriber_message, meta_info):
        return await _voice_listener.handle_transcriber_output(self, next_task, transcriber_message, meta_info)

    async def _report_provider_health(self, service, provider, model, ok, latency_ms=None, phase=None, blocking=False):
        return await _voice_health.report_provider_health(
            self, service, provider, model, ok, latency_ms=latency_ms, phase=phase, blocking=blocking
        )

    def _active_tool(self, kind):
        return _voice_health.active_tool(self, kind)

    def _component_model(self, kind):
        return _voice_health.component_model(self, kind)

    async def _report_component_health(self, service, provider, process_latency_ms, connect_flag):
        return await _voice_health.report_component_health(self, service, provider, process_latency_ms, connect_flag)

    async def _report_stream_connect(self):
        return await _voice_health.report_stream_connect(self)

    async def _end_call_on_component_error(self, error, hangup_detail):
        return await _voice_listener.end_call_on_component_error(self, error, hangup_detail)

    async def _log_transcriber_connection_error(self, connection_error):
        return await _voice_listener.log_transcriber_connection_error(self, connection_error)

    async def _maybe_update_tts_language(self, meta_info):
        return await _voice_listener.maybe_update_tts_language(self, meta_info)

    async def _listen_transcriber(self):
        return await _voice_listener.listen_transcriber(self)

    async def __process_http_transcription(self, message):
        return await _voice_listener.process_http_transcription(self, message)

    def __enqueue_chunk(self, chunk, i, number_of_chunks, meta_info):
        return _voice_output_loop.enqueue_chunk(self, chunk, i, number_of_chunks, meta_info)

    def is_sequence_id_in_current_ids(self, sequence_id):
        return _voice_listener.is_sequence_id_in_current_ids(self, sequence_id)

    # spec-0004 B9a: the language-subsystem bodies (Region Q) live VERBATIM in
    # voiceai.modules.voice.session.language.{lid_gate,switcher,handoff}. Each
    # same-named thin delegator below keeps this class the resolution site
    # (patch.object, __get__-rebinds, __new__ harnesses, fixture doubles and internal
    # self-dispatch via the mangled _TaskManager__* spellings) and injects the session
    # (self, the LanguageSession facade) on every call (§3.1 bridge 3). The class-level
    # lid_playback_gate default above is UNTOUCHED — a pinned behavior-invariant
    # (tests/test_language_switch_race.py asserts it at CLASS level, so it can never
    # become a descriptor); the LanguageSwitchCoordinator's delegating property
    # forwards to the session attribute instead. The three pure evidence readers are
    # bound as staticmethods of the MOVED functions, so
    # TaskManager._TaskManager__buffered_language_evidence(pool, ...) and friends keep
    # resolving by identity. Those modules are now the lookup sites for the moved
    # bodies' globals (the pool classes' isinstance checks, the LANGUAGE_SWITCH_*
    # constants, trailing_utterance_text, build_lid_decision_record,
    # is_alphanumeric_readout, create_ws_data_packet, convert_to_request_log,
    # update_prompt_with_context, audio_to_mulaw8k, audio_to_pcm, LANGUAGE_NAMES,
    # SUPPORTED_OUTPUT_TELEPHONY_HANDLERS, WEBCALL_TTS_SAMPLE_RATE): monkeypatch
    # string paths for those target voiceai.modules.voice.session.language.<module>.<name>.
    # A delegator is deleted only in the commit that ports its pinning tests
    # (spec 0004 iron rule; B9b owns the remaining language test files). The
    # speculation-commit trio below (__log_committed_speculation /
    # __log_discarded_speculation / __speculative_followup_text) deliberately stays in
    # this class: step B10 (the history/interruption commit path) owns its
    # tests/test_speculation_commit_logging.py patch-path repoints (R3).
    def _collect_flux_lid_events(self) -> list:
        return _voice_lid_gate.collect_flux_lid_events(self)

    def __language_switch_enabled(self) -> bool:
        return _voice_lid_gate.language_switch_enabled(self)

    def __arm_lid_playback_gate(self, sequence_id, decision_task) -> None:
        return _voice_lid_gate.arm_lid_playback_gate(self, sequence_id, decision_task)

    def __lid_playback_gate_holds(self, sequence_id) -> bool:
        return _voice_lid_gate.lid_playback_gate_holds(self, sequence_id)

    def __release_lid_playback_gate(self, gate: dict, outcome: str, clear: bool = True) -> None:
        return _voice_lid_gate.release_lid_playback_gate(self, gate, outcome, clear=clear)

    __recent_detected_turns = staticmethod(_voice_lid_gate.recent_detected_turns)
    __detector_corroborates = staticmethod(_voice_lid_gate.detector_corroborates)
    __buffered_language_evidence = staticmethod(_voice_lid_gate.buffered_language_evidence)

    def __switch_decide_timeout_s(self) -> float:
        return _voice_switcher.switch_decide_timeout_s(self)

    def __switch_settle_ms(self) -> int:
        return _voice_switcher.switch_settle_ms(self)

    def __switch_audio_gap_s(self) -> float:
        return _voice_switcher.switch_audio_gap_s(self)

    def _spawn_language_switch_decision(self, transcriber_message: str, meta_info: dict) -> asyncio.Task | None:
        return _voice_switcher.spawn_language_switch_decision(self, transcriber_message, meta_info)

    def __detector_language_mismatch(self) -> bool:
        return _voice_lid_gate.detector_language_mismatch(self)

    def __snapshot_lid_events(self) -> list:
        return _voice_lid_gate.snapshot_lid_events(self)

    def __record_lid_usage(self, pool) -> None:
        return _voice_lid_gate.record_lid_usage(self, pool)

    def __record_lid_event(self, record: dict) -> None:
        return _voice_lid_gate.record_lid_event(self, record)

    async def handle_language_switch(
        self,
        active_transcript: str = "",
        meta_info: dict | None = None,
        spawn_language: str | None = None,
    ) -> None:
        return await _voice_switcher.handle_language_switch(self, active_transcript, meta_info, spawn_language)

    async def __lid_idle_watcher(self):
        return await _voice_lid_gate.lid_idle_watcher(self)

    async def __run_language_switch(
        self,
        active_transcript: str,
        meta_info: dict | None,
        spawn_language: str | None = None,
    ) -> tuple | None:
        return await _voice_switcher.run_language_switch(self, active_transcript, meta_info, spawn_language)

    def __prepare_followup_generation(self, meta_info=None):
        return _voice_switcher.prepare_followup_generation(self, meta_info)

    async def __play_switch_handoff(self, target: str) -> None:
        return await _voice_handoff.play_switch_handoff(self, target)

    def __handoff_text_for(self, label):
        return _voice_handoff.handoff_text_for(self, label)

    def __handoff_mulaw_wire(self) -> bool:
        return _voice_handoff.handoff_mulaw_wire(self)

    async def __prewarm_handoff_clips(self):
        return await _voice_handoff.prewarm_handoff_clips(self)

    def __handoff_clip_convert(self, synth, audio, mulaw_wire):
        return _voice_handoff.handoff_clip_convert(self, synth, audio, mulaw_wire)

    def __language_directive(self, label: str) -> str:
        return _voice_switcher.language_directive(self, label)

    def __apply_language_directive(self, label: str, context_note: str = None) -> None:
        return _voice_switcher.apply_language_directive(self, label, context_note)

    def __log_committed_speculation(self, spec_text: str, capture):
        return _voice_history.log_committed_speculation(self, spec_text, capture)

    def __log_discarded_speculation(self, spec_text: str, capture):
        return _voice_history.log_discarded_speculation(self, spec_text, capture)

    async def __speculative_followup_text(
        self, target_label: str, detector_transcript: str, active_transcript: str = "", idle_user_text: str = ""
    ):
        return await _voice_history.speculative_followup_text(
            self, target_label, detector_transcript, active_transcript, idle_user_text
        )

    async def __generate_switch_followup(self, messages, followup_meta_info, next_step):
        return await _voice_switcher.generate_switch_followup(self, messages, followup_meta_info, next_step)

    async def switch_language(self, label, components=None, triggered_by: str = "manual", context_note: str = None):
        return await _voice_switcher.switch_language(
            self, label, components=components, triggered_by=triggered_by, context_note=context_note
        )

    async def __listen_synthesizer(self):
        all_text_to_be_synthesized = []
        try:
            while not self.conversation_ended:
                logger.info("Listening to synthesizer")
                try:
                    async for message in self.tools["synthesizer"].generate():
                        meta_info = message.get("meta_info", {})
                        current_text = meta_info.get("text", "")
                        write_to_log = False
                        if current_text not in all_text_to_be_synthesized:
                            all_text_to_be_synthesized.append(current_text)
                            write_to_log = True

                        is_first_message = meta_info.get("is_first_message", False)
                        sequence_id = meta_info.get("sequence_id", None)

                        # Check if the message is valid to process
                        if is_first_message or (
                            not self.conversation_ended and self.interruption_manager.is_valid_sequence(sequence_id)
                        ):
                            logger.info(f"Processing message with sequence_id: {sequence_id}")

                            if self.stream:
                                if meta_info.get("is_first_chunk", False):
                                    first_chunk_generation_timestamp = time.time()
                                    # is_first_chunk re-stamps on every frame once the turn's text is flushed.
                                    _ttfb = meta_info.get("synthesizer_latency")
                                    _tts_key = (sequence_id, _ttfb)
                                    if _tts_key != self._cb_tts_last:
                                        self._cb_tts_last = _tts_key
                                        await self._report_component_health(
                                            "synthesizer",
                                            self.synthesizer_provider,
                                            round(_ttfb * 1000) if _ttfb is not None else None,
                                            "_cb_synthesizer_connect_reported",
                                        )

                                if self.tools["output"].process_in_chunks(self.yield_chunks):
                                    number_of_chunks = math.ceil(len(message["data"]) / self.output_chunk_size)
                                    for chunk_idx, chunk in enumerate(
                                        yield_chunks_from_memory(message["data"], chunk_size=self.output_chunk_size)
                                    ):
                                        self.__enqueue_chunk(chunk, chunk_idx, number_of_chunks, meta_info)
                                else:
                                    self.buffered_output_queue.put_nowait(message)
                            else:
                                # Non-streaming output
                                logger.info("Stream not enabled, sending entire audio")
                                # TODO handle is audio playing over here
                                await self.tools["output"].handle(message)
                                if meta_info.get("end_of_synthesizer_stream", False):
                                    self._turn_audio_flushed.set()

                            if write_to_log:
                                logger.info(f"Writing response to log {meta_info.get('text')}")
                                convert_to_request_log(
                                    message=current_text,
                                    meta_info=meta_info,
                                    component=LogComponent.SYNTHESIZER,
                                    direction=LogDirection.RESPONSE,
                                    model=self.synthesizer_provider,
                                    is_cached=meta_info.get("is_cached", False),
                                    engine=self.tools["synthesizer"].get_engine(),
                                    run_id=self.run_id,
                                )
                        else:
                            logger.info(f"Skipping message with sequence_id: {sequence_id}")
                            # A retired sequence's final chunk is skipped here, so its
                            # is_final_chunk mark never reaches Plivo and no mark echo ever
                            # arrives to clear is_audio_being_played. Without this, the flag
                            # latches True forever and every later user utterance is dropped
                            # as a false interruption. Mirror the BLOCK-path guard.
                            if meta_info.get("end_of_synthesizer_stream", False):
                                self._turn_audio_flushed.set()
                                self.tools["input"].update_is_audio_being_played(False)

                        # Give control to other tasks
                        sleep_time = self.tools["synthesizer"].get_sleep_time()
                        await asyncio.sleep(sleep_time)

                except asyncio.CancelledError:
                    logger.info("Synthesizer task was cancelled.")
                    # await self.handle_cancellation("Synthesizer task was cancelled.")
                    self._turn_audio_flushed.set()
                    break
                except Exception as e:
                    self._turn_audio_flushed.set()
                    await self._end_call_on_component_error(
                        SynthesizerError(
                            str(e), provider=self.synthesizer_provider, model=self._component_model("synthesizer")
                        ),
                        HangupReason.SYNTHESIZER_ERROR,
                    )
                    break

            logger.info("Exiting __listen_synthesizer gracefully.")

        except asyncio.CancelledError:
            logger.info("Synthesizer task cancelled outside loop.")
            # await self.handle_cancellation("Synthesizer task was cancelled outside loop.")
        except Exception as e:
            model = self._component_model("synthesizer")
            await self._end_call_on_component_error(
                SynthesizerError(str(e), provider=self.synthesizer_provider, model=model),
                HangupReason.SYNTHESIZER_ERROR,
            )
            raise SynthesizerError(str(e), provider=self.synthesizer_provider, model=model) from e
        finally:
            await self.tools["synthesizer"].cleanup()

    async def __send_preprocessed_audio(self, meta_info, text):
        return await _voice_output_loop.send_preprocessed_audio(self, meta_info, text)

    async def _synthesize(self, message):
        return await _voice_output_loop.synthesize(self, message)

    async def __send_first_message(self, message):
        return await _voice_listener.send_first_message(self, message)

    """
    When the welcome message is playing we accumulate the transcript in the self.transcriber_message variable and once 
    the welcome message is completely played we send this transcript for further processing.
    """

    async def __handle_accumulated_message(self):
        return await _voice_listener.handle_accumulated_message(self)

    # Currently this loop only closes in case of interruption
    # but it shouldn't be the case.
    async def __process_output_loop(self):
        return await _voice_output_loop.process_output_loop(self)

    async def _inject_and_run_llm(self, injected_message: str):
        return await _voice_output_loop.inject_and_run_llm(self, injected_message)

    def _should_stall_hangup(
        self, audio_playing, has_pending_generation, time_since_last_spoken_ai_word, time_since_user_last_spoke
    ):
        """Hang up when the call has made no forward progress at all: no audio playing, nothing
        in flight, and both sides silent past the floor. Runs above the audio/pipeline gate so it
        still applies when a pipeline flag is stuck. Floor stays above hangup_after_silence so the
        normal inactivity path takes precedence whenever it is reachable."""
        if self.hang_conversation_after <= 0:
            return False
        stall_timeout = max(self.hang_conversation_after, STALL_HANGUP_FLOOR_S)
        return (
            not audio_playing
            and not has_pending_generation
            and time_since_last_spoken_ai_word > stall_timeout
            and time_since_user_last_spoke > stall_timeout
        )

    def _pipeline_busy(self, audio_playing):
        """True while the agent is speaking or about to: response_in_pipeline can read False in the gap between a response's synth push and its first audio chunk, so _synthesis_awaiting_first_audio covers it."""
        return audio_playing or self.response_in_pipeline or self._synthesis_awaiting_first_audio

    def compute_last_ai_audio_timestamp(self):
        """Most recent moment agent audio was still reaching the user.

        last_transmitted_timestamp only advances on a turn's final-chunk mark ack, so it stays
        frozen through a long turn and the watchdog scores a still-speaking agent as silent.
        The playout estimate covers that turn, and clamping it to now means the measured silence
        can only shrink, never grow, so this can delay a hangup but never cause an earlier one.
        """
        return max(
            self.last_transmitted_timestamp, min(time.time(), self.mark_event_meta_data.get_audio_playing_until())
        )

    # spec-0004 B7: the completion/backchanneling watchdog bodies live VERBATIM in
    # voiceai.modules.voice.session.lifecycle.hangup (see the lifecycle block above);
    # these same-named delegators keep this class the resolution site — run()'s task
    # creation below, the s2s runner's mangled call site and patch.object(TaskManager,
    # "_TaskManager__check_for_completion", ...) all keep resolving.
    async def __check_for_completion(self):
        return await _voice_hangup.check_for_completion(self)

    async def __check_for_backchanneling(self):
        return await _voice_hangup.check_for_backchanneling(self)

    # spec-0004 B8: bodies live VERBATIM in voiceai.modules.voice.session.welcome (see
    # the welcome block above); these same-named delegators keep this class the
    # resolution site (the init_event_observable registration of handle_init_event
    # included) and inject the session. The WELCOME module is now the lookup site for
    # the welcome bodies' globals (create_ws_data_packet, convert_to_request_log,
    # pcm_to_ulaw, calculate_audio_duration, wav_bytes_to_pcm, get_synth_audio_format,
    # resample, update_prompt_with_context): monkeypatch string paths for those target
    # voiceai.modules.voice.session.welcome.<name>.
    async def __first_message(self, timeout=10.0):
        return await _voice_welcome.first_message(self, timeout=timeout)

    async def handle_init_event(self, init_meta_data):
        return await _voice_welcome.handle_init_event(self, init_meta_data)

    ########################
    # Speech-to-speech conversation
    ########################
    # spec-0004 B5: the S2S bodies (Region U) live VERBATIM in
    # voiceai.modules.voice.session.s2s_runner. Each same-named thin delegator below
    # keeps this class the resolution site for string patches, __new__ harnesses and
    # internal self-dispatch, and injects the session (self, the S2SSession facade)
    # into the runner (§3.1 bridge 3). A delegator is deleted only in the commit that
    # ports its pinning tests (spec 0004 iron rule). Monkeypatch string paths for the
    # bodies' globals (convert_to_request_log, trigger_api, ...) now target the
    # runner module, where those lookups live (R3).

    def _s2s_telephony_provider(self):
        return _voice_s2s_runner._s2s_telephony_provider(self)

    def _s2s_is_carrier_leg(self):
        return _voice_s2s_runner._s2s_is_carrier_leg(self)

    def _s2s_input_format(self):
        return _voice_s2s_runner._s2s_input_format(self)

    def _s2s_output_format(self):
        return _voice_s2s_runner._s2s_output_format(self)

    def _build_s2s_provider(self):
        return _voice_s2s_runner._build_s2s_provider(self)

    async def _run_s2s_conversation(self):
        return await _voice_s2s_runner._run_s2s_conversation(self)

    async def _hangup_after_goodbye(self, reason) -> None:
        return await _voice_s2s_runner._hangup_after_goodbye(self, reason)

    async def _s2s_hangup_if_goodbye_never_comes(self) -> None:
        return await _voice_s2s_runner._s2s_hangup_if_goodbye_never_comes(self)

    def _s2s_track_task(self, task, call_id=None) -> None:
        return _voice_s2s_runner._s2s_track_task(self, task, call_id)

    def _s2s_on_task_done(self, task) -> None:
        return _voice_s2s_runner._s2s_on_task_done(self, task)

    def _s2s_extend_playout(self, chunk: bytes) -> None:
        return _voice_s2s_runner._s2s_extend_playout(self, chunk)

    def _s2s_agent_has_floor(self) -> bool:
        return _voice_s2s_runner._s2s_agent_has_floor(self)

    def _s2s_within_welcome_gate(self):
        return _voice_s2s_runner._s2s_within_welcome_gate(self)

    async def _s2s_audio_ingest_loop(self):
        return await _voice_s2s_runner._s2s_audio_ingest_loop(self)

    async def _s2s_event_loop(self):
        return await _voice_s2s_runner._s2s_event_loop(self)

    def _s2s_encode_output(self, pcm):
        return _voice_s2s_runner._s2s_encode_output(self, pcm)

    def _s2s_meta(self, **extra):
        return _voice_s2s_runner._s2s_meta(self, **extra)

    async def _s2s_drop_queued_audio(self):
        return await _voice_s2s_runner._s2s_drop_queued_audio(self)

    async def _s2s_finish_turn(self, event):
        return await _voice_s2s_runner._s2s_finish_turn(self, event)

    async def _s2s_output_loop(self):
        return await _voice_s2s_runner._s2s_output_loop(self)

    async def _s2s_text_loop(self):
        return await _voice_s2s_runner._s2s_text_loop(self)

    async def _s2s_dtmf_loop(self):
        return await _voice_s2s_runner._s2s_dtmf_loop(self)

    async def _s2s_execute_tool(self, event):
        return await _voice_s2s_runner._s2s_execute_tool(self, event)

    async def _s2s_before_tool_request(self, event, args, params, meta_info):
        return await _voice_s2s_runner._s2s_before_tool_request(self, event, args, params, meta_info)

    async def _s2s_call_api_tool(self, event, args, params, meta_info):
        return await _voice_s2s_runner._s2s_call_api_tool(self, event, args, params, meta_info)

    async def run(self):
        self._component_error = None  # Reset for each run
        self._error_logged = False
        try:
            if self._is_conversation_task():
                logger.info("started running")
                # Create transcriber and synthesizer tasks
                tasks = []
                # tasks = [asyncio.create_task(self.tools['input'].handle())]

                if self.__is_s2s():
                    # One socket replaces the transcriber, LLM and synthesizer legs; the S2S
                    # runner owns its own output, hangup and DTMF tasks.
                    tasks.append(asyncio.create_task(self._run_s2s_conversation()))
                else:
                    # In the case of web call we would play the first message once we receive the init event
                    if self.turn_based_conversation:
                        self.first_message_task = asyncio.create_task(self.__first_message())

                    if not self.turn_based_conversation:
                        self.handle_accumulated_message_task = asyncio.create_task(self.__handle_accumulated_message())
                    if "transcriber" in self.tools:
                        tasks.append(asyncio.create_task(self._listen_transcriber()))
                        self.transcriber_task = asyncio.create_task(self.tools["transcriber"].run())

                    if (
                        self.turn_based_conversation or (self._is_browser_leg() and not self.__is_s2s())
                    ) and self._is_conversation_task():
                        logger.info(
                            "Since it's connected through dashboard, I'll run listen_llm_tas too in case user wants to simply text"
                        )
                        self.llm_queue_task = asyncio.create_task(self._listen_llm_input_queue())

                    if (
                        "synthesizer" in self.tools
                        and self._is_conversation_task()
                        and not self.turn_based_conversation
                    ):
                        try:
                            self.synthesizer_task = asyncio.create_task(self.__listen_synthesizer())
                        except asyncio.CancelledError as e:
                            logger.error(f"Synth task got cancelled {e}")
                            traceback.print_exc()

                    self.output_task = asyncio.create_task(self.__process_output_loop())
                    if not self.turn_based_conversation or self.enforce_streaming:
                        self.hangup_task = asyncio.create_task(self.__check_for_completion())
                        if (
                            self._is_browser_leg()
                            and "transcriber" not in self.tools
                            and "synthesizer" not in self.tools
                            and "s2s" not in self.tools
                        ):
                            # Text-only browser tasks have no media loops, so
                            # gather() below would return instantly and run()
                            # would yield None, crashing the socket handler.
                            # The hangup task ends this wait on hangup/timeout.
                            tasks.append(self.hangup_task)

                        if self.should_backchannel:
                            self.backchanneling_task = asyncio.create_task(self.__check_for_backchanneling())

                        if self.__is_graph_agent():
                            self.event_listener_task = asyncio.create_task(self._listen_events())

                try:
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    # Cancellation is a normal hangup, but it also lands here when a task is
                    # torn down before it ever got going, which is indistinguishable from a
                    # clean finish in the log otherwise.
                    logger.info(f"Conversation tasks cancelled | pending={sum(1 for t in tasks if not t.done())}")
                except Exception as e:
                    if not isinstance(e, VoiceAIComponentError):
                        logger.error(f"Error: {e}")
                    else:
                        # Typed component errors were only ever written to the request log, so
                        # a failed provider connect left nothing in the app log to find.
                        logger.error(
                            f"Component error | component={e.component} provider={e.provider} model={e.model} error={e}"
                        )
                    if self.run_id and not self._error_logged:
                        if isinstance(e, VoiceAIComponentError):
                            error_msg = format_error_message(e.component, e.provider or e.model or "-", str(e))
                            model = e.model or "-"
                        else:
                            error_msg = format_error_message("unknown", "-", str(e))
                            model = "-"
                        convert_to_request_log(
                            error_msg,
                            {"request_id": self.task_id, "sequence_id": None},
                            model=model,
                            component=LogComponent.ERROR,
                            direction=LogDirection.ERROR,
                            is_cached=False,
                            run_id=self.run_id,
                        )
                    self._error_logged = True

                _stored_err = self._component_error
                if _stored_err is not None:
                    self._component_error = None
                    err_cls = _stored_err["cls"]
                    if issubclass(err_cls, VoiceAIComponentError):
                        raise err_cls(
                            _stored_err["message"], provider=_stored_err["provider"], model=_stored_err["model"]
                        )
                    else:
                        raise Exception(_stored_err["message"])
                for attr, cls, provider in [
                    ("synthesizer_task", SynthesizerError, getattr(self, "synthesizer_provider", None)),
                    (
                        "transcriber_task",
                        TranscriberError,
                        # An s2s task stores transcriber as an explicit None, so the key is
                        # present and a default on .get() never fires.
                        (self.task_config.get("tools_config", {}).get("transcriber") or {}).get("provider"),
                    ),
                ]:
                    task = getattr(self, attr, None)
                    if task and task.done() and not task.cancelled():
                        exc = task.exception()
                        if exc is not None:
                            raise cls(str(exc), provider=provider)

                # Drain an in-flight hangup goodbye before the terminal trim
                # (lifecycle.hangup.drain_hangup_goodbye); order pinned behaviorally.
                await _voice_hangup.drain_hangup_goodbye(self)
                logger.info("Conversation completed")
                self.conversation_ended = True
            else:
                # Run agent followup tasks
                try:
                    if self.task_config["task_type"] == "webhook":
                        await self._process_followup_task()
                    else:
                        await self._run_llm_task(self.input_parameters)
                except VoiceAIComponentError:
                    raise
                except Exception as e:
                    raise

        except asyncio.CancelledError as e:
            traceback.print_exc()
            logger.info(f"Websocket got cancelled {self.task_id}")

        except Exception as e:
            # Cancel all tasks on error
            error_message = str(e)
            if not isinstance(e, VoiceAIComponentError):
                logger.error(f"Exception in task manager run: {error_message}")

            # Log call-breaking exception to CSV trace with component attribution (skip if already logged)
            if self.run_id and not self._error_logged:
                meta_info = {"request_id": self.task_id, "sequence_id": None}
                if isinstance(e, VoiceAIComponentError):
                    error_msg = format_error_message(e.component, e.provider or e.model or "-", error_message)
                    model = e.model or "-"
                else:
                    error_msg = format_error_message("unknown", "-", error_message)
                    model = "-"
                convert_to_request_log(
                    error_msg,
                    meta_info,
                    model=model,
                    component=LogComponent.ERROR,
                    direction=LogDirection.ERROR,
                    is_cached=False,
                    run_id=self.run_id,
                )

            logger.info(f"Exception occurred {e}")
            raise

        finally:
            self._component_error = None

            # Cancel llm_task first and await it so that any transfer_end appended
            # in __execute_function_call's finally block is captured before
            # progression_data snapshots transfer_call_events below.
            await process_task_cancellation(self.llm_task, "llm_task")

            # Construct output
            tasks_to_cancel = []
            tasks_to_cancel.append(process_task_cancellation(self.first_message_task_new, "first_message_task_new"))
            tasks_to_cancel.append(process_task_cancellation(self.llm_task, "llm_task"))
            tasks_to_cancel.append(process_task_cancellation(self.llm_queue_task, "llm_queue_task"))
            tasks_to_cancel.append(
                process_task_cancellation(self.execute_function_call_task, "execute_function_call_task")
            )
            tasks_to_cancel.append(process_task_cancellation(self._lid_idle_watcher_task, "lid_idle_watcher_task"))
            tasks_to_cancel.append(process_task_cancellation(self.regen_settle_task, "regen_settle_task"))
            # Sync cancel BEFORE clearing, so an in-flight render can't repopulate the
            if self.handoff_prewarm_task is not None:
                self.handoff_prewarm_task.cancel()
            tasks_to_cancel.append(process_task_cancellation(self.handoff_prewarm_task, "handoff_prewarm_task"))
            self.handoff_audio_cache.clear()
            if "synthesizer" in self.tools and self.synthesizer_task is not None:
                tasks_to_cancel.append(process_task_cancellation(self.synthesizer_task, "synthesizer_task"))
                tasks_to_cancel.append(
                    process_task_cancellation(self.synthesizer_monitor_task, "synthesizer_monitor_task")
                )
                for task in self.synthesizer_tasks:
                    tasks_to_cancel.append(process_task_cancellation(task, "synthesizer_task_item"))
                self.synthesizer_tasks = []

            # Transcriber cleanup
            if "transcriber" in self.tools:
                tasks_to_cancel.append(self.tools["transcriber"].cleanup())
                if hasattr(self, "transcriber_task") and self.transcriber_task is not None:
                    tasks_to_cancel.append(process_task_cancellation(self.transcriber_task, "transcriber_task"))

            # An S2S task has neither transcriber nor synthesizer, but still owes the caller
            # a conversation payload: transcript, hangup detail, recording, progression.
            # Text-only tasks (llm pipeline, no media legs) owe one too — without
            # this, run() yields None and the socket handler crashes on it.
            # B13b: Region V assembly delegates to the lifecycle/report builders over one
            # teardown capture (expression-for-expression per the B6 parity suite); the branch
            # guard below mirrors run()'s verbatim guard via the snapshot.
            _teardown_snap = _voice_report.snapshot_teardown(self)
            if _teardown_snap.wants_conversation_report:
                output = _voice_report.build_conversation_report(_teardown_snap)
                tasks_to_cancel.append(process_task_cancellation(self.output_task, "output_task"))
                tasks_to_cancel.append(process_task_cancellation(self.hangup_task, "hangup_task"))
                tasks_to_cancel.append(process_task_cancellation(self.backchanneling_task, "backchanneling_task"))
                # tasks_to_cancel.append(process_task_cancellation(self.initial_silence_task, 'initial_silence_task'))
                tasks_to_cancel.append(process_task_cancellation(self.first_message_task, "first_message_task"))
                tasks_to_cancel.append(process_task_cancellation(self.dtmf_task, "dtmf_task"))
                tasks_to_cancel.append(process_task_cancellation(self.event_listener_task, "event_listener_task"))
                tasks_to_cancel.append(
                    process_task_cancellation(self.handle_accumulated_message_task, "handle_accumulated_message_task")
                )

                output["recording_url"] = None
                if self.should_record:
                    output["recording_url"] = await save_audio_file_to_s3(
                        self.conversation_recording, self.sampling_rate, self.assistant_id, self.run_id
                    )
            else:
                output = _voice_report.build_followup_report(_teardown_snap)

            try:
                await asyncio.gather(*tasks_to_cancel)
            except Exception as e:
                logger.error(f"Error during task cancellation: {e}")
            finally:
                llm_agents_to_close = set()
                llm_agent = self.tools.get("llm_agent")
                if llm_agent is not None:
                    llm_agents_to_close.add(llm_agent)
                for agent in getattr(self, "llm_agent_map", {}).values():
                    if agent is not None:
                        llm_agents_to_close.add(agent)
                for agent in llm_agents_to_close:
                    if hasattr(agent, "llm") and hasattr(agent.llm, "close"):
                        try:
                            await agent.llm.close()
                        except Exception as e:
                            logger.error(f"Error closing LLM: {e}")
                    for attr in ("conversation_completion_llm", "voicemail_llm"):
                        aux = getattr(agent, attr, None)
                        if aux and hasattr(aux, "close"):
                            try:
                                await aux.close()
                            except Exception as e:
                                logger.error(f"Error closing {attr}: {e}")
                lang_det = getattr(self, "language_detector", None)
                if lang_det:
                    aux_llm = getattr(lang_det, "_llm", None)
                    if aux_llm and hasattr(aux_llm, "close"):
                        try:
                            await aux_llm.close()
                        except Exception as e:
                            logger.error(f"Error closing language detector LLM: {e}")
                for obs in self.observable_variables.values():
                    obs._observers.clear()
                self.observable_variables.clear()
                for tool in self.tools.values():
                    if hasattr(tool, "task_manager_instance"):
                        tool.task_manager_instance = None
                self.tools.clear()
                self.kwargs.pop("task_manager_instance", None)
                self.conversation_recording = {"input": {"data": b""}, "output": [], "metadata": {}}
                self.conversation_history = None
                self.request_logs.clear()
                self.function_tool_api_call_details.clear()

            return output

    async def handle_cancellation(self, message):
        try:
            # Cancel all tasks on cancellation
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            logger.info(f"tasks {len(tasks)}")
            for task in tasks:
                await process_task_cancellation(task, task.get_name())
                logger.info(f"Cancelling task {task.get_name()}")
                task.cancel()
            logger.info(message)
        except Exception as e:
            traceback.print_exc()
            logger.info(e)
