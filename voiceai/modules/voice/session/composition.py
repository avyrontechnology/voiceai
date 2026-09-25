"""Call composition: building a live call session from constructor arguments (spec 0004, B13a).

Region D of ``voiceai/agent_manager/task_manager.py`` (the ``__init__`` wiring) moved
here VERBATIM, split into six source-order phases sharing one ``CallArgs`` bundle:

* ``seed_call_state`` — kwargs capture (incl. the ``task_manager_instance`` backref,
  retired at B13c), callbacks, usage-task sets, timestamps, latency ledgers and flags.
* ``adopt_call_config`` — ``CallConfig.parse`` over the raw kwargs plus every kwargs
  mutation and task_id gate (welcome pops, api_tools/assistant_id/process_interim
  writes, end_call injections, IO-handler setup calls, observables, history ledger).
* ``wire_tasks_and_history`` — task slots, conversation history, label flow.
* ``wire_session_state`` — language detector, sids/metering, llm configs, output
  queue, cache, default interruption manager, request logs, regen/switch maps.
* ``compose_primary_task`` — the ``task_id == 0`` block (IO defaults, DTMF consumer,
  end_call injection, voicemail, backchanneling, welcome preload, interruption
  reconfigure).
* ``compose_runtime_legs`` — transcriber/synthesizer/llm setup dispatch plus the
  language/llm setup tail.

``compose_call_session`` runs the phases in order; ``TaskManager.__init__`` keeps its
exact legacy dict signature and delegates to it, and ``from_components`` is the
alternate entry over an explicit ``CallArgs`` (the post-cutover construction seam).

Seams (the B4-B12 precedent):

* **Narrow facade, injected per call.** Each phase takes the live session as its first
  parameter (kept named ``self``) plus the shared ``args`` bundle, so bodies stay
  byte-identical. ``TaskManager`` injects itself (§3.1 bridge 3).
* **Legacy constructors through the bridge.** ``VoicemailHandler``,
  ``MarkEventMetaData``, ``ObservableVariable``, ``ConversationHistory``,
  ``LanguageDetector``, ``LanguageSwitcher``, ``WebhookAgent``,
  ``get_file_names_in_directory`` and ``ACCIDENTAL_INTERRUPTION_PHRASES`` ride
  ``adapters.composition`` (§3.1 bridge 1); ``InterruptionManager`` rides
  ``session.interruption`` and ``ComponentLatencies`` rides ``voiceai.modules.voice.
  models`` directly (both new architecture). The moved bodies' lookup sites are THIS
  module's globals (R3).
* **The ``tools`` service-locator retires here.** ``self.tools`` is still assigned
  (every moved turn body reads through it), but it is built by composition — no
  caller reaches past it into constructors anymore; ``from_components`` is the only
  other construction entry.

Fourteen compile-time name-mangling accommodations inside otherwise-verbatim bodies
(the B5-B12 precedent): every ``self.__<name>`` dispatch (``__is_s2s``,
``__is_graph_agent``, ``__is_multiagent``, ``__language_switch_enabled``,
``__setup_*``, ``__inject_switch_language_tool``) is spelled
``self._TaskManager__*``, exactly what the class body always compiled to — and it
keeps the ``__setup_*`` methods (which stay on ``TaskManager``) intercepting
dispatch. Signatures gained type annotations (rule 6), public functions gained
docstrings (rule 7), and the module logs through ``otobaai`` (rule 3; log content
preserved). Preserved quirks stay preserved: the InterruptionManager
default-then-reconfigure double construction, the ``os.getenv`` fallbacks
(``CHECK_FOR_COMPLETION_LLM``, ``BACKCHANNELING_PRESETS_DIR`` — rule-4 debt),
the ``yield_chunks=False`` hard override, the backchanneling-audio ``/.lower()``
on a possibly-None voice, and the ``_usage_tasks``/``_cb_tasks`` strong-ref sets
all belong to ``revamp/resilient-core`` (R8) and are never re-fixed here.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from voiceai.common.logger import get_logger
from voiceai.enums import ToolScope
from voiceai.modules.agents import resolve_pipeline_for_task
from voiceai.modules.voice.adapters.composition import (
    ACCIDENTAL_INTERRUPTION_PHRASES,
    ConversationHistory,
    LanguageDetector,
    MarkEventMetaData,
    ObservableVariable,
    VoicemailHandler,
    WebhookAgent,
    get_file_names_in_directory,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.models import ComponentLatencies
from voiceai.modules.voice.session.config import CallConfig
from voiceai.modules.voice.session.interruption import InterruptionManager
from voiceai.modules.voice.static_methods import _inject_end_call_tool

logger = get_logger(MODULE_NAME)

__all__ = [
    "CallArgs",
    "adopt_call_config",
    "compose_call_session",
    "compose_primary_task",
    "compose_runtime_legs",
    "seed_call_state",
    "wire_session_state",
    "wire_tasks_and_history",
]


@dataclass
class CallArgs:
    """The ``TaskManager`` constructor arguments as one bundle (spec 0004, B13a).

    ``from_components`` takes this instead of fourteen positional parameters, so the
    composition root has exactly one construction seam besides the legacy ``__init__``
    signature (which builds the same bundle and delegates). ``task`` and ``kwargs``
    stay open dicts: the engine mutates them during composition (the B1-pinned kwargs
    contract), so freezing or copying would change behavior.
    """

    assistant_name: str
    task_id: int
    task: dict
    ws: Any  # why: the engine treats the websocket opaquely
    input_parameters: Any = None  # why: caller-supplied initial history or None
    context_data: Any = None  # why: recipient/call context is caller-shaped
    assistant_id: Any = None  # why: legacy accepts any identifier-ish value
    turn_based_conversation: bool = False
    cache: Any = None  # why: cache backend is caller-provided
    input_queue: Any = None  # why: queues are caller-provided or built here
    conversation_history: Any = None  # why: history seed is caller-shaped
    output_queue: Any = None  # why: queues are caller-provided or built here
    yield_chunks: bool = True
    kwargs: dict = field(default_factory=dict)


def compose_call_session(self: Any, args: CallArgs) -> Any:  # why: the live session is duck-typed until cutover
    """Run every composition phase in source order over the session and its args.

    Args:
        self: The live call session (the legacy ``TaskManager`` injects itself).
        args: The bundled constructor arguments.

    Returns:
        The parsed ``CallConfig`` (primary-task and runtime-leg phases need it; the
        legacy constructor never exposed it, so returning it changes nothing).
    """
    seed_call_state(self, args)
    call_config = adopt_call_config(self, args)
    wire_tasks_and_history(self, args)
    wire_session_state(self, args, call_config)
    compose_primary_task(self, args, call_config)
    compose_runtime_legs(self, args, call_config)
    return call_config


def seed_call_state(self: Any, args: CallArgs) -> None:  # why: the live session is duck-typed until cutover
    """Capture kwargs and seed timestamps, ledgers, flags and sets."""
    self.kwargs = args.kwargs
    self.kwargs["task_manager_instance"] = self
    # Optional load-signal callback (set by the caller only for PTU-served calls).
    self.on_turn_usage = args.kwargs.get("on_turn_usage")
    # Fired instead of on_turn_usage when another backend served the turn.
    self.on_overflow = args.kwargs.get("on_overflow")
    self._usage_tasks = set()  # strong refs so fire-and-forget tallies aren't GC'd before they run
    # Optional per-provider health callback (circuit-breaker shadow); never affects the call.
    self.on_provider_health = args.kwargs.get("on_provider_health")
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
    self.transcriber_error_events = []
    self.blocked_audio_events = []
    self._blocked_sequences = set()  # dedup: only record first block per sequence
    self._sent_audio_sequences = set()
    self._committed_assistant_sequences = set()

    self.task_config = args.task


def adopt_call_config(self: Any, args: CallArgs) -> CallConfig:  # why: the live session is duck-typed until cutover
    """Parse the call config over the raw kwargs; apply every mutation and task gate."""
    # spec-0004 B4: the parse runs over the RAW kwargs, before the welcome pops below;
    # every kwargs mutation and task_id gate stays in this constructor.
    call_config = CallConfig.parse(
        task=args.task,
        context_data=args.context_data,
        kwargs=self.kwargs,
        turn_based_conversation=args.turn_based_conversation,
    )

    self.timezone = call_config.timezone
    self.language = call_config.language
    self.synthesizer_voice_id = None
    self.synthesizer_model = None
    self.transfer_call_params = call_config.transfer_call_params

    if args.task["tools_config"].get("api_tools", None) is not None:
        self.kwargs["api_tools"] = args.task["tools_config"]["api_tools"]

    # Speech-to-speech agents carry no llm_agent/transcriber/synthesizer at all.
    self.s2s_config = call_config.s2s_config

    llm_agent_cfg = args.task["tools_config"].get("llm_agent") or {}
    # UI sends a flat SimpleLlmAgent ({model, provider, ...}); graph/multi agents nest it
    # under llm_config. Support both so a UI-saved voice agent doesn't KeyError here.
    nested_llm_cfg = llm_agent_cfg.get("llm_config", llm_agent_cfg)
    if nested_llm_cfg.get("assistant_id", None) is not None:
        self.kwargs["assistant_id"] = nested_llm_cfg["assistant_id"]

    logger.info(f"doing task {args.task}")
    self.task_id = args.task_id
    self.assistant_name = args.assistant_name
    self.tools = {}
    self.multilingual_prompts = {}
    self.websocket = args.ws
    self.context_data = args.context_data
    self.turn_based_conversation = args.turn_based_conversation
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
    self.event_queue = args.kwargs.get("event_queue") or asyncio.Queue()
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
    self.assistant_id = args.assistant_id
    self.run_id = call_config.run_id

    self.mark_event_meta_data = MarkEventMetaData()
    self.sampling_rate = call_config.sampling_rate
    self.conversation_ended = False
    self.has_transfer = False
    self.hangup_triggered = False
    self.hangup_triggered_at = None
    self.hangup_decision_at = None
    self._hangup_processing = False
    self.dtmf_events = []
    self.non_fatal_llm_error_events = []
    self._agent_end_timestamps = {}
    self.hangup_message_queued = False
    self._end_of_conversation_in_progress = False
    self._end_call_in_progress = False
    self._turn_audio_flushed = asyncio.Event()
    self._turn_audio_flushed.set()
    self.hangup_mark_event_timeout = 10

    # Prompts
    self.prompts, self.system_prompt = {}, {}
    self.input_parameters = args.input_parameters

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
    if args.task_id == 0:
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

        is_s2s_task = resolve_pipeline_for_task(args.task) == "s2s" and args.task.get(
            "task_type", "conversation"
        ) == "conversation"
        if is_s2s_task:
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

        self._TaskManager__setup_input_handlers(args.turn_based_conversation, args.input_queue, self.should_record)
    self._TaskManager__setup_output_handlers(args.turn_based_conversation, args.output_queue)
    return call_config


def wire_tasks_and_history(self: Any, args: CallArgs) -> None:  # why: the live session is duck-typed until cutover
    """Create task slots, history ledger and label flow."""
    self.first_message_task_new = asyncio.create_task(self.message_task_new())

    self.conversation_history = ConversationHistory(args.conversation_history)
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
    self.response_in_pipeline = False
    self._synthesis_awaiting_first_audio = False
    self._response_turn_id = 0
    self._turn_msg_map = {}  # turn_id → assistant message dict ref in _messages
    self._pending_assistant_history = {}  # sequence_id -> {content, turn_id, response_uid}
    # Replies awaiting forwarding as chat transcript frames. Drained after each
    # turn (browser legs only) — voice-only calls never visit the typed-chat
    # llm queue, so draining only there stranded them until the next typed
    # message flushed the whole backlog at once.
    self._pending_chat_forward = []
    # Recently forwarded transcript lines (bounded): eager speculative turns and
    # the confirming real turn stage identical text — without this the panel
    # would show every reply twice.
    self._forwarded_chat_texts = []


def wire_session_state(self: Any, args: CallArgs, call_config: CallConfig) -> None:
    """Seed detector, sids, metering, llm configs, output queue and interruption defaults."""
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
    self.cache = args.cache

    # Initialize InterruptionManager with defaults (will be reconfigured for task_id == 0)
    self.interruption_manager = InterruptionManager()

    # setup request logs
    self.request_logs = []

    # Stores structured API call records for dashboard/backend persistence.
    self.function_tool_api_call_details = []
    # Records every language switch — manual tool call (legacy) or LLM-driven
    # (triggered_by="lid_llm") — used post-call for precision / latency analysis.
    self.language_switch_events = []
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
    self.transfer_call_events = []
    self.hangup_task = None

    self.conversation_config = None


def compose_primary_task(self: Any, args: CallArgs, call_config: CallConfig) -> None:
    """Compose the task_id == 0 primary task: IO defaults, DTMF, end_call, voicemail, welcome."""
    if args.task_id == 0:
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
        if dtmf_enabled and not call_config.is_s2s:
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
            elif self._TaskManager__is_graph_agent():
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
            self.asked_if_user_is_still_there = False  # Used to make sure that if user's phrase qualifies as acciedental interruption, we don't break the conversation loop  # noqa: E501 — verbatim legacy line (R8)
            self.accidental_interruption_phrases = call_config.accidental_interruption_phrases
            # self.interruption_backoff_period = 1000 #conversation_config.get("interruption_backoff_period", 300) #this is the amount of time output loop will sleep before sending next audio  # noqa: E501

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
            self.backchanneling_message_gap = (
                call_config.backchanneling_message_gap
            )  # Amount of duration co routine will sleep  # noqa: E501 — verbatim legacy line (R8)
            if self.should_backchannel and not args.turn_based_conversation and args.task_id == 0:
                logger.info("Should backchannel")
                self.backchanneling_audios = f"{args.kwargs.get('backchanneling_audio_location', os.getenv('BACKCHANNELING_PRESETS_DIR'))}/{self.synthesizer_voice.lower()}"  # noqa: E501 — verbatim legacy line (R8)
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


def compose_runtime_legs(self: Any, args: CallArgs, call_config: CallConfig) -> None:
    """Dispatch transcriber/synthesizer/llm setup plus the language/llm tail."""
    # setting transcriber and synthesizer in parallel
    if call_config.is_s2s:
        self._TaskManager__setup_s2s()
    else:
        self._TaskManager__setup_transcriber()
        self._TaskManager__setup_synthesizer(self.llm_config)
        if not self.turn_based_conversation and args.task_id == 0 and "synthesizer" in self.tools:
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
    if not self._TaskManager__language_switch_enabled() or judge_dead:
        if judge_dead:
            logger.warning(
                "LanguageSwitcher has no resolvable API key — injecting the legacy switch_language "
                "tool as the fallback switch path for this call"
            )
        self._TaskManager__inject_switch_language_tool()

    # # setting llm
    # llm = self.__setup_llm(self.llm_config)
    # # Setup tasks
    # self.__setup_tasks(llm)

    # setting llm
    if self.llm_config is not None:
        llm = self._TaskManager__setup_llm(self.llm_config, args.task_id)
        # Setup tasks
        agent_params = {"llm": llm, "agent_type": self.llm_agent_config.get("agent_type", "simple_llm_agent")}
        self._TaskManager__setup_tasks(**agent_params)

    elif self._TaskManager__is_multiagent():
        # Setup task for multiagent conversation
        for agent in self.task_config["tools_config"]["llm_agent"]["llm_config"]["agent_map"]:
            if "routes" in self.llm_config_map[agent]:
                del self.llm_config_map[agent]["routes"]  # Remove routes from here as it'll create conflict ahead
            llm = self._TaskManager__setup_llm(self.llm_config_map[agent])
            agent_type = self.llm_config_map[agent].get("agent_type", "simple_llm_agent")
            logger.info(f"Getting response for {llm} and agent type {agent_type} and {agent}")
            agent_params = {"llm": llm, "agent_type": agent_type}
            llm_agent = self._TaskManager__setup_tasks(**agent_params)
            self.llm_agent_map[agent] = llm_agent

    elif self.task_config["task_type"] == "webhook":
        if "webhookURL" in self.task_config["tools_config"]["api_tools"]:
            webhook_url = self.task_config["tools_config"]["api_tools"]["webhookURL"]
        else:
            webhook_url = self.task_config["tools_config"]["api_tools"]["tools_params"]["webhook"]["url"]
        logger.info(f"Webhook URL {webhook_url}")
        self.tools["webhook_agent"] = WebhookAgent(webhook_url=webhook_url)
