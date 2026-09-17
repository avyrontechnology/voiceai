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
    prepare_api_request,
    validate_outbound_url,
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
from voiceai.modules.voice.session.turn import output_loop as _voice_output_loop
from voiceai.modules.voice.session.turn import transcript_listener as _voice_listener
from voiceai.modules.voice.session.turn import history_sync as _voice_history

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
        self.kwargs = kwargs
        self.kwargs["task_manager_instance"] = self
        # Optional load-signal callback (set by the caller only for PTU-served calls).
        self.on_turn_usage = kwargs.get("on_turn_usage")
        # Fired instead of on_turn_usage when another backend served the turn.
        self.on_overflow = kwargs.get("on_overflow")
        self._usage_tasks = set()  # strong refs so fire-and-forget tallies aren't GC'd before they run
        # Optional per-provider health callback (circuit-breaker shadow); never affects the call.
        self.on_provider_health = kwargs.get("on_provider_health")
        self._cb_tasks = set()
        self._cb_transcriber_connect_reported = False
        self._cb_synthesizer_connect_reported = False
        self._cb_stream_reported = False
        self._cb_tts_last = None

        self.conversation_start_init_ts = time.time() * 1000
        self.llm_latencies = ComponentLatencies()
        self.transcriber_latencies = ComponentLatencies()
        self.synthesizer_latencies = ComponentLatencies()
        self.rag_latencies = {"turn_latencies": []}
        self.routing_latencies = {"turn_latencies": []}
        self.stream_sid_ts = None
        self.welcome_message_duration_ms = None
        self.transcriber_error_events: list[dict] = []
        self.blocked_audio_events: list[dict] = []
        self._blocked_sequences: set = set()  # dedup: only record first block per sequence
        self._sent_audio_sequences: set = set()
        self._committed_assistant_sequences: set = set()

        self.task_config = task

        # spec-0004 B4: the parse runs over the RAW kwargs, before the welcome pops below;
        # every kwargs mutation and task_id gate stays in this constructor.
        call_config = CallConfig.parse(
            task=task,
            context_data=context_data,
            kwargs=self.kwargs,
            turn_based_conversation=turn_based_conversation,
        )

        self.timezone = call_config.timezone
        self.language = call_config.language
        self.synthesizer_voice_id = None
        self.synthesizer_model = None
        self.transfer_call_params = call_config.transfer_call_params

        if task["tools_config"].get("api_tools", None) is not None:
            self.kwargs["api_tools"] = task["tools_config"]["api_tools"]

        # Speech-to-speech agents carry no llm_agent/transcriber/synthesizer at all.
        self.s2s_config = call_config.s2s_config

        llm_agent_cfg = task["tools_config"].get("llm_agent") or {}
        # UI sends a flat SimpleLlmAgent ({model, provider, ...}); graph/multi agents nest it
        # under llm_config. Support both so a UI-saved voice agent doesn't KeyError here.
        nested_llm_cfg = llm_agent_cfg.get("llm_config", llm_agent_cfg)
        if nested_llm_cfg.get("assistant_id", None) is not None:
            self.kwargs["assistant_id"] = nested_llm_cfg["assistant_id"]

        logger.info(f"doing task {task}")
        self.task_id = task_id
        self.assistant_name = assistant_name
        self.tools = {}
        self.multilingual_prompts = {}
        self.websocket = ws
        self.context_data = context_data
        self.turn_based_conversation = turn_based_conversation
        self.enforce_streaming = call_config.enforce_streaming
        self.room_url = call_config.room_url
        self.is_web_based_call = call_config.is_web_based_call
        # self.callee_silent = True
        # TODO check if we need to toggle this based on some config
        self.yield_chunks = False
        # Set up communication queues between processes
        self.audio_queue = asyncio.Queue()
        self.llm_queue = asyncio.Queue()
        self.synthesizer_queue = asyncio.Queue()
        self.transcriber_output_queue = asyncio.Queue()
        self.dtmf_queue = asyncio.Queue()
        self.event_queue = kwargs.get("event_queue") or asyncio.Queue()
        self.queues = {
            "dtmf": self.dtmf_queue,
            "events": self.event_queue,
            "transcriber": self.audio_queue,
            "llm": self.llm_queue,
            "synthesizer": self.synthesizer_queue,
        }
        self.pipelines = call_config.pipelines
        self.textual_chat_agent = call_config.textual_chat_agent

        # Assistant persistance stuff
        self.assistant_id = assistant_id
        self.run_id = call_config.run_id

        self.mark_event_meta_data = MarkEventMetaData()
        self.sampling_rate = call_config.sampling_rate
        self.conversation_ended = False
        self.has_transfer = False
        self.hangup_triggered = False
        self.hangup_triggered_at = None
        self.hangup_decision_at = None
        self._hangup_processing = False
        self.dtmf_events: list[dict] = []
        self.non_fatal_llm_error_events: list[dict] = []
        self._agent_end_timestamps: dict = {}
        self.hangup_message_queued = False
        self._end_of_conversation_in_progress = False
        self._end_call_in_progress = False
        self._turn_audio_flushed = asyncio.Event()
        self._turn_audio_flushed.set()
        self.hangup_mark_event_timeout = 10

        # Prompts
        self.prompts, self.system_prompt = {}, {}
        self.input_parameters = input_parameters

        # Recording
        self.should_record = False
        self.conversation_recording = {
            "input": {"data": b"", "started": time.time()},
            "output": [],
            "metadata": {"started": 0},
        }

        # spec-0004 B4: values parsed (and the welcome PCM pre-decoded/upsampled, memoized
        # process-wide) in CallConfig; the pops stay HERE so downstream components never
        # see the base64 blob in kwargs — the kwargs contract the B1 matrix pins.
        self.welcome_message_audio = call_config.welcome_message_audio
        self.kwargs.pop("welcome_message_audio", None)
        self.welcome_message_audio_sample_rate = call_config.welcome_message_audio_sample_rate
        self.kwargs.pop("welcome_message_audio_sample_rate", None)

        self.welcome_message_delay = call_config.welcome_message_delay
        self.preloaded_welcome_audio = call_config.preloaded_welcome_audio
        self.observable_variables = {}
        self.output_handler_set = False
        # IO HANDLERS
        if task_id == 0:
            # UI-saved voice agents persist input/output as null when telephony is
            # unconfigured. Default them so the browser leg gets Default handlers
            # instead of a TypeError on ["provider"] below.
            tools_config = self.task_config["tools_config"]
            if not (tools_config.get("input") or {}).get("provider"):
                tools_config["input"] = {"provider": "default", "format": "wav"}
            if not (tools_config.get("output") or {}).get("provider"):
                tools_config["output"] = {"provider": "default", "format": "wav"}
            if self.is_web_based_call:
                self.task_config["tools_config"]["input"]["provider"] = "default"
                self.task_config["tools_config"]["output"]["provider"] = "default"

            if self.__is_s2s():
                # Browser-leg realtime agents carry no telephony handlers (input /
                # output are null in the record). Without this, every direct
                # tools_config["input"]["provider"] access below throws, and no
                # IO handlers exist for the websocket leg. Default routing gives
                # them the JSON-speaking Default handlers (16k PCM up, 24k down).
                tools_config = self.task_config["tools_config"]
                if not (tools_config.get("input") or {}).get("provider"):
                    tools_config["input"] = {"provider": "default", "format": "wav"}
                if not (tools_config.get("output") or {}).get("provider"):
                    tools_config["output"] = {"provider": "default", "format": "wav"}

            self.default_io = self.task_config["tools_config"]["output"]["provider"] == "default"
            self.observable_variables["agent_hangup_observable"] = ObservableVariable(False)
            self.observable_variables["agent_hangup_observable"].add_observer(self.agent_hangup_observer)

            self.observable_variables["final_chunk_played_observable"] = ObservableVariable(False)
            self.observable_variables["final_chunk_played_observable"].add_observer(self.final_chunk_played_observer)

            if self.is_web_based_call:
                self.observable_variables["init_event_observable"] = ObservableVariable(None)
                self.observable_variables["init_event_observable"].add_observer(self.handle_init_event)

            # TODO revert this temporary change for web based call
            if self.is_web_based_call:
                self.should_record = False
            else:
                self.should_record = (
                    self.task_config["tools_config"]["output"]["provider"] == "default" and self.enforce_streaming
                )  # In this case, this is a websocket connection and we should record

            self.__setup_input_handlers(turn_based_conversation, input_queue, self.should_record)
        self.__setup_output_handlers(turn_based_conversation, output_queue)

        self.first_message_task_new = asyncio.create_task(self.message_task_new())

        self.conversation_history = ConversationHistory(conversation_history)
        self.label_flow = []

        # Setup IO SERVICE, TRANSCRIBER, LLM, SYNTHESIZER
        self.llm_task = None
        self._inflight_llm_asr_turn_id = None
        self.eager_llm_task = None
        self.eager_history_snapshot = None
        self.eager_meta_info = None
        self.llm_queue_task = None
        self.execute_function_call_task = None
        # Set while a tool call executes so a parallel LID switch won't truncate it.
        self.function_call_in_flight = False
        self.synthesizer_tasks = []
        self.synthesizer_task = None
        self._component_error = None
        self._error_logged = False
        self.synthesizer_monitor_task = None
        self.dtmf_task = None
        self.event_listener_task = None

        # state of conversation
        self.current_request_id = None
        self.previous_request_id = None
        self.llm_rejected_request_ids = set()
        self.llm_processed_request_ids = set()
        self.buffers = []
        self.should_respond = False
        self.last_response_time = time.time()
        self.consider_next_transcript_after = time.time()
        self.llm_response_generated = False
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        self._response_turn_id = 0
        self._turn_msg_map = {}  # turn_id → assistant message dict ref in _messages
        self._pending_assistant_history = {}  # sequence_id -> {content, turn_id, response_uid}
        # Replies awaiting forwarding as chat transcript frames. Drained after each
        # turn (browser legs only) — voice-only calls never visit the typed-chat
        # llm queue, so draining only there stranded them until the next typed
        # message flushed the whole backlog at once.
        self._pending_chat_forward: list = []
        # Recently forwarded transcript lines (bounded): eager speculative turns and
        # the confirming real turn stage identical text — without this the panel
        # would show every reply twice.
        self._forwarded_chat_texts: list = []

        # Language detection
        self.language_detector = LanguageDetector(self.task_config["task_config"], run_id=self.run_id)
        self.language_injection_mode = call_config.language_injection_mode
        self.language_instruction_template = call_config.language_instruction_template

        # Call conversations
        self.call_sid = None
        self.stream_sid = None

        # metering
        self.transcriber_duration = 0
        self.synthesizer_characters = 0
        self.ended_by_assistant = False
        # False until the caller's own words land in conversation history as a user turn.
        # Synthetic user turns (silence nudges, injected prompts) deliberately do not set it,
        # so a call where only the agent ever spoke stays False.
        self.user_spoke = False
        self.start_time = time.time()

        # Tasks
        self.extracted_data = None
        self.summarized_data = None
        self.stream = call_config.stream

        self.is_local = False
        self.llm_config = None
        self.agent_type = None

        # spec-0004 B4: the llm_agent parse (multiagent map / kb / graph / simple, the
        # reasoning-key passthrough, use_responses_api, compact_threshold) lives in
        # CallConfig._llm_configs with the same reference semantics; llm_agent_config is
        # assigned only when the legacy branches assigned it.
        self.llm_config_map = call_config.llm_config_map
        self.llm_agent_map = {}
        if call_config.llm_agent_config is not None:
            self.llm_agent_config = call_config.llm_agent_config
        self.llm_config = call_config.llm_config

        # Output stuff
        self.output_task = None
        self.buffered_output_queue = asyncio.Queue()

        # Memory
        self.cache = cache

        # Initialize InterruptionManager with defaults (will be reconfigured for task_id == 0)
        self.interruption_manager = InterruptionManager()

        # setup request logs
        self.request_logs = []

        # Stores structured API call records for dashboard/backend persistence.
        self.function_tool_api_call_details = []
        # Records every language switch — manual tool call (legacy) or LLM-driven
        # (triggered_by="lid_llm") — used post-call for precision / latency analysis.
        self.language_switch_events: list[dict] = []
        # Debounce for overlapped finals: one regen per merged utterance, not per fragment.
        self.regen_settle_task = None
        self.regen_settle_payload = None
        # Legacy-flow handoff state (populated by __inject_switch_language_tool
        # when the LLM-driven switch flow is NOT enabled for this call).
        self.switch_handoff_messages = {}
        self.agent_names = {}
        # Dedicated LLM that decides language switches from the unbiased detector
        # transcript. Set up in __setup_transcriber only for gated multilingual agents.
        self.language_switcher = None
        # Serializes switch decisions so overlapping turns can't interleave two
        # switch+follow-up sequences. Background-only: the caller-facing pipeline
        # (ASR -> main LLM -> TTS) never waits on this lock.
        self.language_switch_lock = asyncio.Lock()
        # Copy of the most recent turn's meta_info — template for the idle-flush
        # fallback's follow-up generation, where no main turn (and thus no meta_info)
        # exists because the locked ASR couldn't decode the caller's speech.
        self._last_turn_meta_info = None
        self._lid_idle_watcher_task = None
        # Handoff clips pre-rendered per language on that language's OWN voice (text and
        self.handoff_audio_cache = {}
        self.handoff_prewarm_task = None
        # In-flight speculative follow-up generation;
        # single slot is safe because decisions are serialized by language_switch_lock.
        self._spec_followup_task = None
        self.transfer_call_events: list[dict] = []
        self.hangup_task = None

        self.conversation_config = None

        if task_id == 0:
            # An S2S task carries no synthesizer block; its voice lives on the s2s config.
            self.synthesizer_voice = call_config.synthesizer_voice
            self.hangup_detail = None
            self.end_call_primary = False  # set below if task_config opts in

            self.handle_accumulated_message_task = None
            # self.initial_silence_task = None
            self.hangup_task = None
            self.transcriber_task = None
            self.output_chunk_size = 16384 if self.sampling_rate == 24000 else 4096  # 0.5 second chunk size for calls
            # For nitro
            self.nitro = True
            self.conversation_config = call_config.conversation_config
            logger.info(f"Conversation config {self.conversation_config}")

            # Enable DTMF flow
            dtmf_enabled = call_config.dtmf_enabled
            # An s2s task starts its own consumer in _run_s2s_conversation. Starting this one
            # too would race it for the same queue, and this one wins by being first: the
            # digits get injected into the transcriber/LLM pipeline an s2s agent does not have.
            if dtmf_enabled and not self.__is_s2s():
                self.tools["input"].is_dtmf_active = True
                self.dtmf_task = asyncio.create_task(self.inject_digits_to_conversation())

            self.trigger_user_online_message_after = call_config.trigger_user_online_message_after
            self.check_if_user_online = call_config.check_if_user_online
            # Parsed (context-substituted) in CallConfig; assigned here unchanged.
            self.check_user_online_message_config = call_config.check_user_online_message_config

            self.kwargs["process_interim_results"] = call_config.process_interim_results

            # for long pauses and rushing
            if self.conversation_config is not None:
                # TODO need to get this for azure - for azure the subtraction would not happen
                # No transcriber on an S2S task: the provider owns endpointing.
                self.minimum_wait_duration = call_config.minimum_wait_duration
                self.last_spoken_timestamp = time.time() * 1000
                self.incremental_delay = call_config.incremental_delay

                # Cut conversation
                self.hang_conversation_after = call_config.hang_conversation_after
                self.last_transmitted_timestamp = 0

                self.use_fillers = call_config.use_fillers
                self.use_llm_to_determine_hangup = call_config.use_llm_to_determine_hangup
                # Parsed in CallConfig (default prompt + the verbatim JSON-format suffix).
                self.check_for_completion_prompt = call_config.check_for_completion_prompt

                # Parsed (context-substituted, web calls excluded) in CallConfig.
                self.call_hangup_message_config = call_config.call_hangup_message_config
                self.check_for_completion_llm = os.getenv("CHECK_FOR_COMPLETION_LLM")

                # spec-0004 B4: description/primary/nodes parsed in CallConfig; the
                # kwargs-mutating injections stay HERE, structure and logs unchanged.
                self.end_call_primary = call_config.end_call_primary
                if self.end_call_primary:
                    self.kwargs["api_tools"] = _inject_end_call_tool(
                        self.kwargs.get("api_tools"),
                        scope=ToolScope.GLOBAL,
                        nodes=[],
                        description=call_config.end_call_description,
                    )
                    logger.info("end_call tool active as primary hangup")
                elif self.__is_graph_agent():
                    # a node opting in via function_call="end_call" needs the tool regardless of the hangup toggle
                    end_call_nodes = call_config.end_call_nodes
                    if end_call_nodes:
                        self.kwargs["api_tools"] = _inject_end_call_tool(
                            self.kwargs.get("api_tools"),
                            scope=ToolScope.NODE,
                            nodes=end_call_nodes,
                            description=call_config.end_call_description,
                        )
                        logger.info(f"end_call tool injected node-scoped on nodes={end_call_nodes}")

                # Voicemail detection (time-based)
                output_tool_available = (
                    "output" in self.tools
                    and self.tools["output"]
                    and self.tools["output"].requires_custom_voicemail_detection()
                )
                self.voicemail_handler = VoicemailHandler(self, self.conversation_config, output_tool_available)

                self.time_since_last_spoken_human_word = 0

                self.repeat_after_silence_seconds = None

                # Handling accidental interruption
                self.number_of_words_for_interruption = call_config.number_of_words_for_interruption
                self.asked_if_user_is_still_there = False  # Used to make sure that if user's phrase qualifies as acciedental interruption, we don't break the conversation loop
                self.started_transmitting_audio = False
                self.accidental_interruption_phrases = call_config.accidental_interruption_phrases
                # self.interruption_backoff_period = 1000 #conversation_config.get("interruption_backoff_period", 300) #this is the amount of time output loop will sleep before sending next audio
                self.allow_extra_sleep = False  # It'll help us to back off as soon as we hear interruption for a while

                # Initialize InterruptionManager to centralize interruption logic
                self.interruption_manager = InterruptionManager(
                    number_of_words_for_interruption=self.number_of_words_for_interruption,
                    accidental_interruption_phrases=ACCIDENTAL_INTERRUPTION_PHRASES,
                    incremental_delay=self.incremental_delay,
                    minimum_wait_duration=self.minimum_wait_duration,
                )

                # Backchanneling presets are keyed on a synthesizer voice, which s2s has none of.
                self.should_backchannel = call_config.should_backchannel
                self.backchanneling_task = None
                self.backchanneling_start_delay = call_config.backchanneling_start_delay
                self.backchanneling_message_gap = call_config.backchanneling_message_gap  # Amount of duration co routine will sleep
                if self.should_backchannel and not turn_based_conversation and task_id == 0:
                    logger.info(f"Should backchannel")
                    self.backchanneling_audios = f"{kwargs.get('backchanneling_audio_location', os.getenv('BACKCHANNELING_PRESETS_DIR'))}/{self.synthesizer_voice.lower()}"
                    # self.num_files = list_number_of_wav_files_in_directory(self.backchanneling_audios)
                    try:
                        self.filenames = get_file_names_in_directory(self.backchanneling_audios)
                        logger.info(f"Backchanneling audio location {self.backchanneling_audios}")
                    except Exception as e:
                        logger.error(f"Something went wrong an putting should backchannel to false {e}")
                        self.should_backchannel = False
                else:
                    self.backchanneling_audio_map = []
                # Agent welcome message
                if "agent_welcome_message" in self.kwargs:
                    logger.info(f"Agent welcome message: {self.kwargs['agent_welcome_message']}")
                    self.first_message_task = None
                    self.transcriber_message = ""

                # Discard pre-welcome utterance
                self.discard_pre_welcome_utterance = call_config.discard_pre_welcome_utterance
                self._speech_started_before_welcome = False

        # setting transcriber and synthesizer in parallel
        if self.__is_s2s():
            self.__setup_s2s()
        else:
            self.__setup_transcriber()
            self.__setup_synthesizer(self.llm_config)
            if not self.turn_based_conversation and task_id == 0 and "synthesizer" in self.tools:
                self.synthesizer_monitor_task = asyncio.create_task(self.tools["synthesizer"].monitor_connection())

        # Language switching, gated per call by the LANGUAGE_SWITCH feature flag
        # (tools_config["llm_language_switch"], see __language_switch_enabled):
        #   enabled  → dedicated Switch LLM driven by the unbiased Saaras v3
        #              detector (handle_language_switch); the main LLM carries
        #              no switch tool.
        #   disabled → legacy flow: switch_language tool on the main LLM + the
        #              pool's per-segment LID heuristic (master behavior).
        # Handoff messages are consumed by BOTH flows (legacy: before the tool
        # switch; new: played to cover the switch → follow-up generation gap).
        self.switch_handoff_messages = call_config.switch_handoff_messages
        self.agent_names = call_config.agent_names
        # LEGACY FLOW ONLY, matching the design comment above: with the Switch LLM enabled the
        # judge is the single switching authority. Injecting the tool alongside it made the main
        # LLM a second, competing switcher deciding from main-ASR text — which mis-scripts foreign
        # speech precisely when switching matters (QA 5765dd9f: tool switched to 'ta' from
        # Tamil-rendered text while the unbiased detector heard 'te'), and its silent history-side
        # races produced unexplained "Already speaking in X" tool responses in every QA round.
        # judge_dead: no API key resolved — re-inject the legacy tool so a flagged agent
        # keeps SOME switch path instead of a judge that fails every decide.
        judge_dead = self.language_switcher is not None and not getattr(self.language_switcher, "has_credentials", True)
        if not self.__language_switch_enabled() or judge_dead:
            if judge_dead:
                logger.warning(
                    "LanguageSwitcher has no resolvable API key — injecting the legacy switch_language "
                    "tool as the fallback switch path for this call"
                )
            self.__inject_switch_language_tool()

        # # setting llm
        # llm = self.__setup_llm(self.llm_config)
        # # Setup tasks
        # self.__setup_tasks(llm)

        # setting llm
        if self.llm_config is not None:
            llm = self.__setup_llm(self.llm_config, task_id)
            # Setup tasks
            agent_params = {"llm": llm, "agent_type": self.llm_agent_config.get("agent_type", "simple_llm_agent")}
            self.__setup_tasks(**agent_params)

        elif self.__is_multiagent():
            # Setup task for multiagent conversation
            for agent in self.task_config["tools_config"]["llm_agent"]["llm_config"]["agent_map"]:
                if "routes" in self.llm_config_map[agent]:
                    del self.llm_config_map[agent]["routes"]  # Remove routes from here as it'll create conflict ahead
                llm = self.__setup_llm(self.llm_config_map[agent])
                agent_type = self.llm_config_map[agent].get("agent_type", "simple_llm_agent")
                logger.info(f"Getting response for {llm} and agent type {agent_type} and {agent}")
                agent_params = {"llm": llm, "agent_type": agent_type}
                llm_agent = self.__setup_tasks(**agent_params)
                self.llm_agent_map[agent] = llm_agent

        elif self.task_config["task_type"] == "webhook":
            if "webhookURL" in self.task_config["tools_config"]["api_tools"]:
                webhook_url = self.task_config["tools_config"]["api_tools"]["webhookURL"]
            else:
                webhook_url = self.task_config["tools_config"]["api_tools"]["tools_params"]["webhook"]["url"]
            logger.info(f"Webhook URL {webhook_url}")
            self.tools["webhook_agent"] = WebhookAgent(webhook_url=webhook_url)

    @staticmethod
    def _sanitize_api_call_headers(headers):
        if not isinstance(headers, dict):
            return headers

        redacted_headers = {}
        sensitive_keys = {"authorization", "proxy-authorization", "x-api-key", "api-key"}
        for key, value in headers.items():
            if str(key).lower() in sensitive_keys:
                redacted_headers[key] = "<redacted>"
            else:
                redacted_headers[key] = value
        return redacted_headers

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

        Called from both the function-call and regular-text branches of
        __do_llm_generation so the two paths stay in sync automatically.
        """
        latency_dict["turn_id"] = meta_info.get("turn_id")
        latency_dict["llm_start_ms"] = (
            round(meta_info.get("llm_start_time", 0) * 1000 - self.conversation_start_init_ts, 2)
            if meta_info.get("llm_start_time")
            else None
        )
        _t = self.tools.get("transcriber")
        if hasattr(_t, "transcribers") and hasattr(_t, "active_label"):
            _t = _t.transcribers.get(_t.active_label, _t)
        latency_dict["asr_turn_id"] = getattr(_t, "turn_counter", None)
        latency_dict["model"] = self.llm_config.get("model") if self.llm_config else None
        latency_dict["input_tokens"] = actual_input_tokens
        latency_dict["output_tokens"] = actual_output_tokens
        latency_dict["reasoning_tokens"] = actual_reasoning_tokens
        latency_dict["cached_tokens"] = actual_cached_tokens
        if response_text:
            latency_dict["response_text"] = response_text.strip()

    @staticmethod
    def _extract_api_call_runtime_args(resp):
        excluded_keys = {"model_response", "textual_response"}
        return {key: copy.deepcopy(value) for key, value in resp.items() if key not in excluded_keys}

    def _build_call_context(self):
        """Common call-state fields included in the pre-call webhook payload.

        These mirror the identifiers carried by the platform's customer-facing
        call-state event webhooks (execution_id/agent_id/provider/numbers) — NOT the
        transfer-specific or internal fields (call_sid/stream_sid are excluded there).
        """
        recipient_data = (self.context_data or {}).get("recipient_data") or {}
        return {
            "execution_id": self.run_id,
            "agent_id": self.assistant_id,
            "provider": self.tools["input"].io_provider,
            "from_number": recipient_data.get("from_number"),
            "to_number": recipient_data.get("to_number"),
        }

    def fire_pre_call_webhook(self, webhook_url, called_fun, resp, meta_info, webhook_param=None):
        """Fire-and-forget pre-call webhook before the tool's main request runs.

        ``params`` = the ``pre_call_webhook_param`` template substituted with the LLM args
        (else empty). Two delivery modes:
          * If ``PRE_CALL_WEBHOOK_DISPATCH_URL`` is set, POST {execution_id, webhook_url,
            params} to it; the backend enriches with the full execution record + params and
            forwards to the customer's webhook_url.
          * Otherwise (fallback), POST directly to the customer's webhook_url with
            params + the common call-state fields.
        Never blocks or fails the main tool call: background task, errors swallowed.
        """
        excluded = {"model_response", "textual_response", "tool_call_id", "resp"}
        llm_args = {key: copy.deepcopy(value) for key, value in resp.items() if key not in excluded}

        # Default missing %(name)s placeholders to "" so one absent field doesn't crash the
        # whole substitution and wipe the payload.
        params = {}
        if webhook_param:
            template_str = webhook_param if isinstance(webhook_param, str) else json.dumps(webhook_param)
            substitution_args = {name: "" for name in re.findall(r"%\((\w+)\)s", template_str)}
            substitution_args.update(llm_args)
            try:
                prepared = prepare_api_request(webhook_param, None, None, **substitution_args)
                if prepared.get("api_params") is not None:
                    params = prepared["api_params"]
            except Exception as exc:
                logger.warning(f"pre_call_webhook_param substitution failed: {exc}")

        dispatch_url = os.getenv("PRE_CALL_WEBHOOK_DISPATCH_URL")
        if dispatch_url:
            # Backend dispatch: it fetches the execution record and merges params.
            target_url = dispatch_url
            payload = {"execution_id": self.run_id, "webhook_url": webhook_url, "params": params}
        else:
            # Fallback: post directly to the customer URL with params + common call-state fields.
            target_url = webhook_url
            payload = {**params, **self._build_call_context()}

        # Record in function_tool_api_call_details so the pre-call webhook lands in the
        # same per-call S3 record as the other API/tool calls.
        api_call_detail = self._start_api_call_detail(
            called_fun=f"{called_fun}:pre_call_webhook",
            url=target_url,
            method="POST",
            param=None,
            headers={"Content-Type": "application/json"},
            meta_info=meta_info,
            runtime_args={"tool_call_id": resp.get("tool_call_id", "")},
            request_body=json.dumps(payload),
            api_params=payload,
        )

        async def send():
            try:
                # ``target_url`` is user-controlled only on the direct fallback path;
                # the dispatch URL is operator-set env config and may be internal.
                if not dispatch_url:
                    await validate_outbound_url(target_url)
                convert_to_request_log(
                    str(payload),
                    meta_info,
                    None,
                    LogComponent.FUNCTION_CALL,
                    direction=LogDirection.REQUEST,
                    run_id=self.run_id,
                )
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    # allow_redirects=False: a redirect hop is not re-validated and would
                    # reopen the SSRF path past the pre-flight check above.
                    async with session.post(target_url, json=payload, allow_redirects=False) as response:
                        response_text = await response.text()
                        logger.info(f"pre_call_webhook response ({response.status}): {response_text}")
                        convert_to_request_log(
                            str(response_text),
                            meta_info,
                            None,
                            LogComponent.FUNCTION_CALL,
                            direction=LogDirection.RESPONSE,
                            run_id=self.run_id,
                        )
                        self._finalize_api_call_detail(
                            api_call_detail,
                            response=response_text,
                            status_code=response.status,
                            content_type=response.headers.get("Content-Type"),
                        )
            except Exception as exc:
                logger.warning(f"pre_call_webhook to {target_url} failed (ignored): {exc}")
                self._finalize_api_call_detail(api_call_detail, error=exc)

        # Keep a strong reference so the task isn't garbage-collected before the POST
        # finishes (the loop only holds a weak ref); drop it once done. Lazy-init the set
        # so this never depends on __init__ (robust to merge churn).
        if not hasattr(self, "background_tasks"):
            self.background_tasks = set()
        task = asyncio.create_task(send())
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

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
        api_call_detail = {
            "tool_name": called_fun,
            "tool_call_id": runtime_args.get("tool_call_id", ""),
            "url": url,
            "method": method.upper() if isinstance(method, str) else method,
            "request_template": copy.deepcopy(param),
            "request_body": copy.deepcopy(request_body),
            "request_params": copy.deepcopy(api_params if api_params is not None else runtime_args),
            "runtime_args": copy.deepcopy(runtime_args),
            "headers": self._sanitize_api_call_headers(copy.deepcopy(headers)),
            "meta": {
                "request_id": meta_info.get("request_id"),
                "sequence_id": meta_info.get("sequence_id"),
                "turn_id": meta_info.get("turn_id"),
            },
            "started_at": datetime.now().isoformat(),
            "status": "pending",
            "response_status_code": None,
            "response_content_type": None,
            "response_body": None,
            "response_json": None,
        }
        self.function_tool_api_call_details.append(api_call_detail)
        return api_call_detail

    @staticmethod
    def _finalize_api_call_detail(api_call_detail, response=None, status_code=None, content_type=None, error=None):
        if api_call_detail is None:
            return

        completed_at = datetime.now()
        api_call_detail["completed_at"] = completed_at.isoformat()
        api_call_detail["latency_ms"] = None
        started_at = api_call_detail.get("started_at")
        if started_at:
            try:
                started_at_dt = datetime.fromisoformat(started_at)
                api_call_detail["latency_ms"] = round((completed_at - started_at_dt).total_seconds() * 1000, 2)
            except ValueError:
                logger.warning(f"Could not compute api call latency from started_at={started_at}")
        if error is not None:
            api_call_detail["status"] = "error"
            api_call_detail["error"] = str(error)
        else:
            api_call_detail["status"] = "completed"
        api_call_detail["response_status_code"] = status_code
        api_call_detail["response_content_type"] = content_type
        api_call_detail["response_body"] = copy.deepcopy(response)
        try:
            api_call_detail["response_json"] = (
                json.loads(response) if isinstance(response, str) else copy.deepcopy(response)
            )
        except (TypeError, json.JSONDecodeError):
            api_call_detail["response_json"] = None

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

        Browser legs only; telephony has no transcript panel and the dashboard
        flow ignores these frames. Never raises — a transcript must not kill a call.
        asr_turn_id lets the panel update one bubble per caller turn instead of
        appending every cumulative re-emission.
        """
        if not text or not str(text).strip():
            return
        if not self._is_browser_leg():
            return
        # Bounded recent-set: eager speculative turns and the confirming real turn
        # stage identical text (also survives __new__-built managers in tests).
        sent = getattr(self, "_forwarded_chat_texts", None)
        if sent is None:
            sent = self._forwarded_chat_texts = []
        if str(text).strip() in sent:
            return
        output = (self.tools or {}).get("output")
        if output is None or getattr(output, "handle", None) is None:
            return
        packet = create_ws_data_packet(
            str(text), {"type": "text", "role": role, "sequence_id": -1, "asr_turn_id": asr_turn_id}
        )
        try:
            await output.handle(packet)
        except Exception as e:
            logger.debug(f"Browser transcript forward failed: {e}")
            return
        logger.info(f"Browser-leg chat reply forwarded | role={role} chars={len(str(text))}")
        sent.append(str(text).strip())
        del sent[:-50]

    async def _drain_pending_chat_forward(self):
        """Flush staged agent replies to the browser transcript panel."""
        if not self._is_browser_leg():
            return
        pending = getattr(self, "_pending_chat_forward", None) or []
        self._pending_chat_forward = []
        for text in pending:
            await self._forward_browser_text(text, "agent")

    # def __is_knowledge_agent(self):
    #     if self.task_config["task_type"] == "webhook":
    #         return False
    #     agent_type = self.task_config['tools_config']["llm_agent"].get("agent_type", None)
    #     return agent_type == "knowledge_agent"

    def _invalidate_response_chain(self):
        return _voice_history.invalidate_response_chain(self)

    def _set_interruption_hint(self, heard_text):
        return _voice_history.set_interruption_hint(self, heard_text)

    def _cancel_in_flight_llm_response(self):
        return _voice_history.cancel_in_flight_llm_response(self)

    def _inject_language_instruction(self, messages: list) -> list:
        """Inject language instruction into messages based on detected language."""
        lang = self.language_detector.dominant_language
        if not lang or not self.language_injection_mode or not self.language_instruction_template:
            return messages

        try:
            lang_name = LANGUAGE_NAMES.get(lang, lang)
            instruction = self.language_instruction_template.format(language=lang_name) + "\n\n"

            if self.language_injection_mode == "system_only":
                for i, msg in enumerate(messages):
                    if msg.get("role") == "system":
                        messages[i]["content"] = instruction + msg["content"]
                        logger.info(f"[system_only] Injected: {lang_name} ({lang})")
                        break
            elif self.language_injection_mode == "per_turn":
                for i, msg in enumerate(messages):
                    if msg.get("role") == "user":
                        messages[i]["content"] = instruction + msg["content"]
                logger.info(
                    f"[per_turn] Injected to {sum(1 for m in messages if m.get('role') == 'user')} user messages: {lang_name} ({lang})"
                )
        except Exception as e:
            logger.error(f"Language injection error: {e}")

        return messages

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

        LEGACY flow only (call site gates on __language_switch_enabled): it is the sole
        switch mechanism there. In the LLM-driven flow the judge is the single switching
        authority and the main LLM carries no switch tool."""
        has_pool = isinstance(self.tools.get("transcriber"), TranscriberPool) or isinstance(
            self.tools.get("synthesizer"), SynthesizerPool
        )
        if not has_pool:
            return

        # Collect available labels from pools
        labels = set()
        if isinstance(self.tools.get("transcriber"), TranscriberPool):
            labels.update(self.tools["transcriber"].labels)
        if isinstance(self.tools.get("synthesizer"), SynthesizerPool):
            labels.update(self.tools["synthesizer"].labels)

        # Enrich the tool schema with available labels in the description
        tool_def = copy.deepcopy(SWITCH_LANGUAGE_TOOL_DEFINITION)
        custom_description = self.task_config.get("tools_config", {}).get("switch_tool_description")
        if custom_description:
            tool_def["function"]["description"] = custom_description
        lang_prop = tool_def["function"]["parameters"]["properties"]["language"]
        lang_prop["enum"] = sorted(labels)
        lang_prop["description"] = f"Language to switch to. Available: {sorted(labels)}"

        if self.kwargs.get("api_tools") is None:
            self.kwargs["api_tools"] = {"tools": [], "tools_params": {}}

        self.kwargs["api_tools"]["tools"].append(tool_def)
        # Entry must exist in tools_params so ToolCallAccumulator.build_api_payload
        # doesn't drop the call, but no pre_call_message — the switch is silent.
        # (switch_handoff_messages / agent_names are loaded for both flows at the
        # setup call site, before this injection.)
        self.kwargs["api_tools"]["tools_params"]["switch_language"] = {}
        logger.info(f"Injected switch_language tool (labels={sorted(labels)})")

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
        if agent_type == "simple_llm_agent":
            llm_agent = StreamingContextualAgent(llm)
        elif agent_type == "graph_agent":
            logger.info("Setting up graph agent with rag-proxy-server support")
            llm_config = self.task_config["tools_config"]["llm_agent"].get("llm_config", {})
            rag_server_url = self.kwargs.get("rag_server_url", os.getenv("RAG_SERVER_URL", "http://localhost:8000"))

            logger.info(f"Graph agent config: {llm_config}")
            logger.info(f"RAG server URL: {rag_server_url}")

            # Set RAG server URL in environment for GraphAgent to use
            os.environ["RAG_SERVER_URL"] = rag_server_url

            # Inject provider credentials for routing and response generation
            injected_cfg = dict(llm_config)
            if "llm_key" in self.kwargs:
                injected_cfg["llm_key"] = self.kwargs["llm_key"]
            if "base_url" in self.kwargs:
                injected_cfg["base_url"] = self.kwargs["base_url"]

            # Pass context_data for variable replacement in node prompts
            if self.context_data:
                injected_cfg["context_data"] = self.context_data

            if "api_version" in self.kwargs:
                injected_cfg["api_version"] = self.kwargs["api_version"]
            if "api_tools" in self.kwargs:
                injected_cfg["api_tools"] = self.kwargs["api_tools"]
            if "reasoning_effort" in self.kwargs:
                injected_cfg["reasoning_effort"] = self.kwargs["reasoning_effort"]
            if "reasoning_summary" in self.kwargs:
                injected_cfg["reasoning_summary"] = self.kwargs["reasoning_summary"]
            if "service_tier" in self.kwargs:
                injected_cfg["service_tier"] = self.kwargs["service_tier"]
            if "overflow_llm" in self.kwargs:
                injected_cfg["overflow_llm"] = self.kwargs["overflow_llm"]
            if "routing_reasoning_effort" in self.kwargs:
                injected_cfg["routing_reasoning_effort"] = self.kwargs["routing_reasoning_effort"]
            if "routing_max_tokens" in self.kwargs:
                injected_cfg["routing_max_tokens"] = self.kwargs["routing_max_tokens"]
            # Set when the caller serves the conversation LLM from a different backend than the agent's own.
            for key in ("aux_model", "aux_provider", "route_routing_to_conversation"):
                if key in self.kwargs:
                    injected_cfg[key] = self.kwargs[key]
            if self.llm_config.get("use_responses_api"):
                injected_cfg["use_responses_api"] = True
            if self.llm_config.get("compact_threshold"):
                injected_cfg["compact_threshold"] = self.llm_config["compact_threshold"]
            injected_cfg["buffer_size"] = self.task_config["tools_config"]["synthesizer"].get("buffer_size")
            injected_cfg["language"] = self.language
            injected_cfg["turn_based_conversation"] = self.turn_based_conversation
            injected_cfg["execution_id"] = self.run_id

            llm_agent = GraphAgent(injected_cfg)
            logger.info("Graph agent created with rag-proxy-server support")
        elif agent_type == "knowledgebase_agent":
            logger.info("Setting up knowledge agent with rag-proxy-server support")
            llm_config = self.task_config["tools_config"]["llm_agent"].get("llm_config", {})
            rag_server_url = self.kwargs.get("rag_server_url", os.getenv("RAG_SERVER_URL", "http://localhost:8000"))

            logger.info(f"Knowledge agent config: {llm_config}")
            logger.info(f"RAG server URL: {rag_server_url}")

            # Set RAG server URL in environment for KnowledgeAgent to use
            os.environ["RAG_SERVER_URL"] = rag_server_url

            # Inject provider credentials and endpoints into KnowledgeAgent config
            injected_cfg = dict(llm_config)
            if "llm_key" in self.kwargs:
                injected_cfg["llm_key"] = self.kwargs["llm_key"]
            if "base_url" in self.kwargs:
                injected_cfg["base_url"] = self.kwargs["base_url"]
            if "api_version" in self.kwargs:
                injected_cfg["api_version"] = self.kwargs["api_version"]
            if "api_tools" in self.kwargs:
                injected_cfg["api_tools"] = self.kwargs["api_tools"]
            if "reasoning_effort" in self.kwargs:
                injected_cfg["reasoning_effort"] = self.kwargs["reasoning_effort"]
            if "reasoning_summary" in self.kwargs:
                injected_cfg["reasoning_summary"] = self.kwargs["reasoning_summary"]
            if "service_tier" in self.kwargs:
                injected_cfg["service_tier"] = self.kwargs["service_tier"]
            if "overflow_llm" in self.kwargs:
                injected_cfg["overflow_llm"] = self.kwargs["overflow_llm"]
            if self.llm_config.get("use_responses_api"):
                injected_cfg["use_responses_api"] = True
            if self.llm_config.get("compact_threshold"):
                injected_cfg["compact_threshold"] = self.llm_config["compact_threshold"]
            injected_cfg["buffer_size"] = self.task_config["tools_config"]["synthesizer"].get("buffer_size")
            injected_cfg["language"] = self.language

            llm_agent = KnowledgeBaseAgent(injected_cfg)
            logger.info("Knowledge agent created with rag-proxy-server support")
        else:
            raise f"{agent_type} Agent type is not created yet"
        return llm_agent

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
        # This is used in case there's silence from callee's side
        if meta_info is None:
            meta_info = self.tools["transcriber"].get_meta_info()
            logger.info(f"Metainfo {meta_info}")
        meta_info_copy = meta_info.copy()

        new_sequence_id = self.interruption_manager.get_next_sequence_id()
        meta_info_copy["sequence_id"] = new_sequence_id
        # Transcript/tool-call grouping needs a response-turn id that is stable
        # across all chunks and sub-steps of one response chain, but independent
        # from audio sequencing. sequence_id is for interruption/audio gating;
        # chunk_id is for chunking; turn_id is for transcript/history grouping.
        self._response_turn_id += 1
        meta_info_copy["turn_id"] = self._response_turn_id
        response_uid = str(uuid.uuid4())
        meta_info_copy["response_uid"] = response_uid
        meta_info_copy["response_group_uid"] = response_uid
        meta_info_copy.pop("parent_response_uid", None)
        logger.info(
            "VOICEAI_TRACE_META new_response seq=%s turn=%s response_uid=%s group_uid=%s request_id=%s origin=%s",
            meta_info_copy.get("sequence_id"),
            meta_info_copy.get("turn_id"),
            meta_info_copy.get("response_uid"),
            meta_info_copy.get("response_group_uid"),
            meta_info_copy.get("request_id"),
            meta_info_copy.get("origin"),
        )

        return meta_info_copy

    def _spawn_followup_meta_info(self, meta_info):
        followup_meta_info = self.__get_updated_meta_info(meta_info)
        followup_meta_info["response_group_uid"] = meta_info.get("response_group_uid") or meta_info.get("response_uid")
        followup_meta_info["parent_response_uid"] = meta_info.get("response_uid")
        for key in (
            "chunk_id",
            "mark_id",
            "is_first_chunk",
            "is_first_chunk_of_entire_response",
            "is_final_chunk_of_entire_response",
            "end_of_synthesizer_stream",
            "end_of_llm_stream",
            "text_synthesized",
        ):
            followup_meta_info.pop(key, None)
        logger.info(
            "VOICEAI_TRACE_META followup seq=%s turn=%s response_uid=%s group_uid=%s parent_response_uid=%s request_id=%s parent_seq=%s parent_turn=%s",
            followup_meta_info.get("sequence_id"),
            followup_meta_info.get("turn_id"),
            followup_meta_info.get("response_uid"),
            followup_meta_info.get("response_group_uid"),
            followup_meta_info.get("parent_response_uid"),
            followup_meta_info.get("request_id"),
            meta_info.get("sequence_id"),
            meta_info.get("turn_id"),
        )
        return followup_meta_info

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
                        self.first_message_passing_time = None
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

                # _listen_transcriber can exit (transcriber idle-closes) while a hangup
                # goodbye is still playing in llm_task; let it drain before trimming.
                if self.hangup_triggered and not self.conversation_ended:
                    await self.wait_for_current_message()

                has_pending_marks = len(self.mark_event_meta_data.mark_event_meta_data) > 0
                has_response_heard = bool(self.tools["input"].response_heard_by_user)
                if has_pending_marks or has_response_heard:
                    await self.sync_history(
                        self.mark_event_meta_data.mark_event_meta_data.items(),
                        time.time(),
                        extend_with_playback_estimate=True,
                    )
                self.tools["input"].reset_response_heard_by_user()
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
            _has_asr_tts = "transcriber" in self.tools and "synthesizer" in self.tools
            _is_text_only = (
                self._is_conversation_task()
                and not _has_asr_tts
                and "s2s" not in self.tools
                and "output" in self.tools
            )
            if self._is_conversation_task() and (_has_asr_tts or "s2s" in self.tools or _is_text_only):
                if _has_asr_tts:
                    self.transcriber_latencies.connection_latency_ms = self.tools["transcriber"].connection_time
                    self.synthesizer_latencies.connection_latency_ms = self.tools["synthesizer"].connection_time

                    self.transcriber_latencies.turn_latencies = self.tools["transcriber"].turn_latencies
                    self.synthesizer_latencies.turn_latencies = self.tools["synthesizer"].turn_latencies
                elif "s2s" in self.tools:
                    # One socket covers both legs, so its timings land on the LLM component.
                    self.llm_latencies.connection_latency_ms = self.tools["s2s"].connection_time
                    self.llm_latencies.turn_latencies = self.tools["s2s"].turn_latencies

                # Annotate each transcriber turn with was_interrupted so callers
                # can see which ASR turns had a user barge-in without cross-referencing
                # the separate interruption_events list.
                _call_start_ms = self.conversation_start_init_ts

                _interrupted_ids = self.interruption_manager.interrupted_transcriber_turn_ids
                for _turn in self.transcriber_latencies.turn_latencies:
                    _tid = _turn.get("turn_id")
                    _turn["was_interrupted"] = _tid in _interrupted_ids if _tid is not None else False
                    if _turn.get("asr_start_epoch_ms") is not None:
                        _turn["asr_start_ms"] = round(_turn.pop("asr_start_epoch_ms") - _call_start_ms, 2)
                    if _turn.get("asr_finalized_epoch_ms") is not None:
                        _turn["asr_finalized_ms"] = round(_turn.pop("asr_finalized_epoch_ms") - _call_start_ms, 2)
                    if _turn.get("asr_turn_start_epoch_ms") is not None:
                        _turn["asr_turn_start_ms"] = round(_turn.pop("asr_turn_start_epoch_ms") - _call_start_ms, 2)
                    if _turn.get("user_speech_end_epoch_ms") is not None:
                        _turn["user_speech_end_ms"] = round(_turn.pop("user_speech_end_epoch_ms") - _call_start_ms, 2)

                # Collect language detection latency if available
                if hasattr(self, "language_detector") and self.language_detector.latency_data:
                    detection_entry = self.language_detector.latency_data
                    detected_epoch_ms = detection_entry.pop("detected_at_epoch_ms", None)
                    if detected_epoch_ms is not None:
                        detection_entry["ts_ms"] = round(detected_epoch_ms - _call_start_ms, 2)
                    self.llm_latencies.other_latencies.append(detection_entry)

                welcome_message_sent_ts = self.tools["output"].get_welcome_message_sent_ts()
                _user_bot_latencies = [
                    {
                        "sequence_id": e["sequence_id"],
                        "user_start_ms": round(e["user_start_s"] * 1000 - _call_start_ms, 2)
                        if e.get("user_start_s") and e["user_start_s"] > 0
                        else None,
                        "user_first_start_ms": round(e["user_first_start_s"] * 1000 - _call_start_ms, 2)
                        if e.get("user_first_start_s") and e["user_first_start_s"] > 0
                        else None,
                        "user_end_ms": round(e["user_end_s"] * 1000 - _call_start_ms, 2)
                        if e.get("user_end_s") is not None
                        else None,
                        "agent_start_ms": round(e["agent_start_s"] * 1000 - _call_start_ms, 2),
                        "latency_ms": e["latency_ms"],
                        # agent_end_ms: mark ACK from provider (actual playback end, most accurate).
                        # None when the final mark was never ACKed (e.g. call dropped mid-audio).
                        "agent_end_ms": (
                            round(e["agent_end_s"] * 1000 - _call_start_ms, 2)
                            if e.get("agent_end_s") is not None
                            else None
                        ),
                    }
                    for e in self.interruption_manager.user_bot_latencies
                ]

                # Cancel the voicemail check BEFORE latency_dict/progression_data are snapshotted
                # below — tasks_to_cancel is only awaited after the snapshot, so a cancelled-check
                # record appended during that gather would never be persisted.
                await process_task_cancellation(self.voicemail_handler.check_task, "voicemail_check_task")

                output = {
                    "messages": self._prepare_precise_transcript_messages(self.history),
                    "conversation_time": time.time() - self.start_time,
                    "label_flow": self.label_flow,
                    "function_tool_api_call_details": copy.deepcopy(self.function_tool_api_call_details),
                    "lid_detection_events": list(self.__snapshot_lid_events()),
                    "asr_lid_events": self._collect_flux_lid_events(),
                    "language_switch_events": list(self.language_switch_events),
                    "call_sid": self.call_sid,
                    "stream_sid": self.stream_sid,
                    "transcriber_duration": self.transcriber_duration,
                    "synthesizer_characters": (
                        self.tools["synthesizer"].get_synthesized_characters() if _has_asr_tts else 0
                    ),
                    "ended_by_assistant": self.ended_by_assistant,
                    "user_spoke": self.user_spoke,
                    "latency_dict": {
                        "llm_latencies": self.llm_latencies.model_dump(),
                        "transcriber_latencies": self.transcriber_latencies.model_dump(),
                        "synthesizer_latencies": self.synthesizer_latencies.model_dump(),
                        "rag_latencies": self.rag_latencies,
                        "routing_latencies": self.routing_latencies,
                        "welcome_message_sent_ts": None,
                        "stream_sid_ts": None,
                        "interruption_stats": self.interruption_manager.get_interruption_stats(
                            self.conversation_start_init_ts
                        ),
                        "user_bot_latencies": _user_bot_latencies,
                        "mark_tracking": self.mark_event_meta_data.get_mark_tracking_summary(),
                        "synthesizer_chunk_marks": self.mark_event_meta_data.get_chunk_marks(),
                    },
                    "hangup_detail": self.hangup_detail,
                    "has_transfer": self.has_transfer,
                }

                try:
                    if welcome_message_sent_ts:
                        output["latency_dict"]["welcome_message_sent_ts"] = (
                            welcome_message_sent_ts - self.conversation_start_init_ts
                        )
                    if self.stream_sid_ts:
                        output["latency_dict"]["stream_sid_ts"] = self.stream_sid_ts - self.conversation_start_init_ts
                except Exception as e:
                    logger.error(f"error in logging audio latency ts {str(e)}")

                output["progression_data"] = {
                    "call_start_epoch_ms": self.conversation_start_init_ts,
                    # Ground-truth transcript so the dashboard renders directly, not from latency gaps.
                    "messages": copy.deepcopy(output["messages"]),
                    "llm_latencies": copy.deepcopy(output["latency_dict"]["llm_latencies"]),
                    "transcriber_latencies": copy.deepcopy(output["latency_dict"]["transcriber_latencies"]),
                    "synthesizer_latencies": copy.deepcopy(output["latency_dict"]["synthesizer_latencies"]),
                    "rag_latencies": output["latency_dict"]["rag_latencies"],
                    "routing_latencies": copy.deepcopy(output["latency_dict"]["routing_latencies"]),
                    "welcome_message_sent_ts": output["latency_dict"]["welcome_message_sent_ts"],
                    "welcome_message_duration_ms": self.welcome_message_duration_ms,
                    "interruption_stats": output["latency_dict"]["interruption_stats"],
                    "user_bot_latencies": copy.deepcopy(output["latency_dict"]["user_bot_latencies"]),
                    "mark_tracking": output["latency_dict"]["mark_tracking"],
                    # Only record of when template speech (are-you-still-there, tool fillers,
                    # handoffs, goodbyes) was actually spoken — it has no LLM turn to anchor to.
                    # Shared ref like mark_tracking — get_chunk_marks builds fresh dicts, nothing mutates them.
                    "synthesizer_chunk_marks": output["latency_dict"]["synthesizer_chunk_marks"],
                    "hangup_triggered_ms": round(self.hangup_triggered_at * 1000 - self.conversation_start_init_ts, 2)
                    if self.hangup_triggered_at
                    else None,
                    "hangup_detail": self.hangup_detail.value if self.hangup_detail else None,
                    "hangup_decision_ms": round(self.hangup_decision_at * 1000 - self.conversation_start_init_ts, 2)
                    if self.hangup_decision_at
                    else None,
                    "voicemail_detected": self.voicemail_handler.detected,
                    "voicemail_check_count": self.voicemail_handler.check_count,
                    "dtmf_events": list(self.dtmf_events),
                    "non_fatal_llm_error_events": list(self.non_fatal_llm_error_events),
                    "language_switch_events": list(self.language_switch_events),
                    "transfer_call_events": list(self.transfer_call_events),
                    "lid_detection_events": list(self.__snapshot_lid_events()),
                    "asr_lid_events": self._collect_flux_lid_events(),
                    "transcriber_error_events": list(self.transcriber_error_events),
                    "transcriber_reconnect_count": getattr(self.tools.get("transcriber"), "reconnect_count", 0),
                    "blocked_audio_events": list(self.blocked_audio_events),
                    "welcome_message_played_ts": (
                        round(
                            self.tools["input"].welcome_message_played_ts - self.conversation_start_init_ts,
                            2,
                        )
                        if getattr(self.tools.get("input"), "welcome_message_played_ts", None)
                        else None
                    ),
                }

                # In progression_data: promote asr_turn_id to turn_id on LLM entries so the
                # progression service can group by turn_id directly without seq indirection.
                # Also stamp turn_id on user_bot_latencies using the same asr_turn map.
                _seq_to_asr_turn: dict = {}
                for _lt in output["progression_data"]["llm_latencies"].get("turn_latencies", []):
                    _seq = _lt.get("sequence_id")
                    _asr_tid = _lt.get("asr_turn_id")
                    if _seq is not None and _asr_tid is not None:
                        _seq_to_asr_turn[_seq] = _asr_tid
                        _lt["turn_id"] = _asr_tid  # overwrite _response_turn_id with asr_turn_id

                for _ub in output["progression_data"]["user_bot_latencies"]:
                    _seq = _ub.get("sequence_id")
                    if _seq is not None and _seq in _seq_to_asr_turn:
                        _ub["turn_id"] = _seq_to_asr_turn[_seq]

                # Stamp a user-speech record for unanswered/interrupted turns too (agent_start_ms stays None).
                _ub_turns = {
                    _ub.get("turn_id")
                    for _ub in output["progression_data"]["user_bot_latencies"]
                    if _ub.get("turn_id") is not None
                }
                for _tt in output["progression_data"]["transcriber_latencies"].get("turn_latencies", []):
                    # _ub_turns holds ints; "turn_3" in {3} is always False, which duplicated every
                    # covered Whisper turn. Compare/store as int (progression copy only).
                    _tid = asr_id_to_int(_tt.get("turn_id"))
                    _tt["turn_id"] = _tid
                    if _tid is None or _tid in _ub_turns or not _tt.get("final_transcript"):
                        continue
                    _u_start = _tt.get("asr_turn_start_ms")
                    if _u_start is None:
                        _u_start = _tt.get("asr_start_ms")
                    # No fallback to asr_finalized_ms: that is when ASR returned, not when the
                    # caller stopped. Substituting it made "ASR time" compute to exactly 0 and
                    # could place the end before the start. None means unknown.
                    _u_end = _tt.get("user_speech_end_ms")
                    output["progression_data"]["user_bot_latencies"].append(
                        {
                            "turn_id": _tid,
                            "sequence_id": None,
                            "user_start_ms": _u_start,
                            "user_first_start_ms": _u_start,
                            "user_end_ms": _u_end,
                            "agent_start_ms": None,
                            "agent_end_ms": None,
                            "latency_ms": None,
                        }
                    )

                for _tts_t in output["progression_data"]["synthesizer_latencies"].get("turn_latencies", []):
                    _seq = _tts_t.get("sequence_id")
                    if _seq is not None and _seq in _seq_to_asr_turn:
                        _tts_t["turn_id"] = _seq_to_asr_turn[_seq]

                # Strip PR-added fields from latency_dict sub-dicts so latency_dict stays at master state.
                # progression_data (deep-copied above) keeps the full enriched versions.
                _llm_turns = (output["latency_dict"]["llm_latencies"] or {}).get("turn_latencies", [])
                for _t in _llm_turns:
                    for _f in (
                        "turn_id",
                        "llm_start_ms",
                        "response_text",
                        "asr_turn_id",
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "cached_tokens",
                        "model",
                        "connection_latency_ms",
                    ):
                        _t.pop(_f, None)

                _asr_turns = (output["latency_dict"]["transcriber_latencies"] or {}).get("turn_latencies", [])
                for _t in _asr_turns:
                    for _f in (
                        "asr_start_ms",
                        "asr_finalized_ms",
                        "asr_turn_start_ms",
                        "user_speech_end_ms",
                    ):
                        _t.pop(_f, None)

                _tts_turns = (output["latency_dict"]["synthesizer_latencies"] or {}).get("turn_latencies", [])
                for _t in _tts_turns:
                    for _f in ("tts_start_ms", "message_category"):
                        _t.pop(_f, None)

                for _e in output["latency_dict"]["user_bot_latencies"]:
                    for _f in ("user_first_start_ms", "agent_end_ms"):
                        _e.pop(_f, None)

                _routing_turns = (output["latency_dict"]["routing_latencies"] or {}).get("turn_latencies", [])
                for _t in _routing_turns:
                    _t.pop("routing_end_ms", None)

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
                output = self.input_parameters
                if self.task_config["task_type"] == "extraction":
                    output = {
                        "extracted_data": self.extracted_data,
                        "task_type": "extraction",
                        "latency_dict": {"llm_latencies": self.llm_latencies.model_dump()},
                    }
                elif self.task_config["task_type"] == "summarization":
                    logger.info(f"self.summarized_data {self.summarized_data}")
                    output = {
                        "summary": self.summarized_data,
                        "task_type": "summarization",
                        "latency_dict": {"llm_latencies": self.llm_latencies.model_dump()},
                    }
                elif self.task_config["task_type"] == "webhook":
                    output = {"status": self.webhook_response, "task_type": "webhook"}

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
