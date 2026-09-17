"""Port-conformance suite for the voice module (spec 0004, step B0).

Import-only and offline, per the B0 gate: the six legacy classes the spec names —
`TranscriberPool`, `SynthesizerPool`, `DefaultInputHandler`, `TelephonyOutputHandler`,
`MarkEventMetaData`, `BaseS2SProvider` — are proven against the ports by TYPED
ASSIGNMENT (a mypy structural check) plus `runtime_checkable` `isinstance` checks on
instances built entirely from in-memory fakes. Nothing here opens a connection, runs an
event loop task, or instantiates a provider (the conftest socket guard would fail the
suite loudly if it did).

The step-B2 ports (`ActiveTranscriberProbePort`, `WelcomeStateSetterPort`,
`SequenceGatePort`'s synthesizer seam) are proven on fakes AND, since B2 landed, on the
legacy classes themselves (`TranscriberPool` probe members, the input handlers'
`set_welcome_message_played`, `BaseSynthesizer`'s preferred `sequence_gate`). This
file also carries the B0 module-def assertions (empty router; register rewritten at B4
from the no-op pin to the VoiceCallService binding) and the skeleton behavior tests for
`models`/`helpers`/`utils`/`exceptions`, since B0's test ownership is exactly this file
(deviation noted in the step report).
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, cast

import pytest

import voiceai.constants as legacy_constants
from voiceai.agent_manager.task_manager import TaskManager, build_lid_decision_record
from voiceai.enums import HangupReason
from voiceai.exceptions import SynthesizerError as LegacySynthesizerError
from voiceai.exceptions import TranscriberError as LegacyTranscriberError
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData
from voiceai.helpers.utils import create_ws_data_packet
from voiceai.input_handlers.default import DefaultInputHandler
from voiceai.input_handlers.telephony import TelephonyInputHandler
from voiceai.modules import ALL_MODULES, ModuleDef, voice
from voiceai.modules.voice.constants import (
    CATEGORY_IS_USER_ONLINE,
    COMPONENT_SYNTHESIZER,
    COMPONENT_TRANSCRIBER,
    LID_FLOW_LLM_SWITCH,
    LID_PATH_TURN_BOUNDARY,
    MODULE_NAME,
    SEQUENCE_ID_ALWAYS_SEND,
)
from voiceai.modules.voice.errors import (
    SynthesisError,
    TranscriptionError,
    UnknownComponentLabelError,
    VoiceError,
)
from voiceai.modules.voice.exceptions import ensure_label_known
from voiceai.modules.voice.helpers import packet_view, transcriber_event_view, turn_meta_from_meta_info
from voiceai.modules.voice.models import HangupDetail, LatencyReport, LidDecisionRecord
from voiceai.modules.voice.utils import epoch_seconds, new_mark_id
from voiceai.modules.voice.ports import (
    ActiveTranscriberProbePort,
    AgentBrainPort,
    CallInputPort,
    CallOutputPort,
    GraphBrainPort,
    LlmPort,
    MarkLedgerPort,
    S2SPort,
    SequenceGatePort,
    SynthesisPoolPort,
    SynthesisPort,
    TranscriptionPoolPort,
    TranscriptionPort,
    WelcomeStateSetterPort,
)
from voiceai.output_handlers.telephony import TelephonyOutputHandler
from voiceai.s2s.base_s2s import BaseS2SProvider
from voiceai.synthesizer.base_synthesizer import BaseSynthesizer
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.transcriber.transcriber_pool import TranscriberPool

if TYPE_CHECKING:  # annotation only: the spy stands in for a container at runtime
    from voiceai.core.container import Container

ACTIVE_LABEL = "english"

# --- Typed assignments: the mypy structural half of the B0 conformance gate -------------
# Each line is a compile-time proof that the legacy class satisfies the port; `make
# check`'s mypy run fails on any drift. No instance is created here.
_TRANSCRIBER_POOL_CONFORMS: Final[type[TranscriptionPoolPort]] = TranscriberPool
_SYNTHESIZER_POOL_CONFORMS: Final[type[SynthesisPoolPort]] = SynthesizerPool
_INPUT_HANDLER_CONFORMS: Final[type[CallInputPort]] = DefaultInputHandler
_OUTPUT_HANDLER_CONFORMS: Final[type[CallOutputPort]] = TelephonyOutputHandler
_MARK_LEDGER_CONFORMS: Final[type[MarkLedgerPort]] = MarkEventMetaData
#: TaskManager itself is the sequence gate today (`is_sequence_id_in_current_ids`).
_SEQUENCE_GATE_CONFORMS: Final[type[SequenceGatePort]] = TaskManager
#: Step B2 landed: the pool carries the three probe members as first-class surface.
_TRANSCRIBER_POOL_PROBE_CONFORMS: Final[type[ActiveTranscriberProbePort]] = TranscriberPool
#: Step B2 landed: the input handlers carry the welcome-state setter.
_INPUT_HANDLER_WELCOME_CONFORMS: Final[type[WelcomeStateSetterPort]] = DefaultInputHandler

# --- Port member pins: renaming or moving a member must be loud -------------------------
TRANSCRIPTION_POOL_METHODS = frozenset(
    {"run", "switch", "reconnect_active", "toggle_connection", "cleanup", "take_lid_transcript"}
)
SYNTHESIS_POOL_METHODS = frozenset({"push", "switch", "generate", "flush_synthesizer_stream", "cleanup"})
INPUT_PORT_METHODS = frozenset(
    {"is_audio_being_played_to_user", "welcome_message_played", "get_response_heard_by_user", "get_stream_sid"}
)
OUTPUT_PORT_METHODS = frozenset({"handle", "handle_interruption", "close", "is_closed", "reopen"})
MARK_LEDGER_METHODS = frozenset({"update_data", "fetch_data", "drop_data", "clear_data", "record_ack"})
S2S_PORT_METHODS = frozenset({"connect", "send_audio", "receive_events", "trigger_response", "disconnect"})
PROBE_PORT_MEMBERS = frozenset({"current_turn_id", "eager_eot_threshold", "supports_regen_settle"})


def _standby_component() -> SimpleNamespace:
    """An inert in-memory stand-in for a pooled transcriber/synthesizer instance."""
    return SimpleNamespace(connection_time=None, turn_latencies=[])


def _transcriber_pool() -> TranscriberPool:
    """Build a real TranscriberPool around fakes — pure construction, no IO."""
    return TranscriberPool({ACTIVE_LABEL: _standby_component()}, None, None, ACTIVE_LABEL, {})


def _synthesizer_pool() -> SynthesizerPool:
    """Build a real SynthesizerPool around fakes — pure construction, no IO."""
    return SynthesizerPool({ACTIVE_LABEL: _standby_component()}, ACTIVE_LABEL, {})


class _StubS2SProvider(BaseS2SProvider):
    """Concrete no-op subclass: proves the inherited base surface, never connects."""

    input_sample_rate = 16000

    async def connect(self):
        """No-op: conformance never opens a session."""

    async def send_audio(self, pcm_bytes):
        """No-op audio sink."""

    async def receive_events(self):
        """Yield nothing; the async-generator shape is what the port pins."""
        return
        yield  # pragma: no cover - makes this an async generator without emitting

    async def send_function_result(self, call_id, name, result):
        """No-op tool-result sink."""

    async def commit_function_results(self):
        """No-op flush."""

    async def trigger_response(self, instructions=None):
        """No-op trigger."""

    async def disconnect(self):
        """No-op close."""


def _stub_s2s_provider() -> _StubS2SProvider:
    """Instantiate the stub provider (attribute assignment only, no IO)."""
    return _StubS2SProvider(system_prompt="", voice="test-voice", model="test-model", api_key="test-key")  # noqa: S106


class FakeActiveTranscriberProbe:
    """Conformer to the step-B2 probe port, with concrete (non-Mock) values."""

    def __init__(self) -> None:
        self.current_turn_id: int | str | None = 3
        self.eager_eot_threshold: float | None = 0.7

    def supports_regen_settle(self) -> bool:
        """Concrete capability answer."""
        return True


class FakeWelcomeSetter:
    """Conformer to the step-B2 welcome-state setter port."""

    def __init__(self) -> None:
        self.played: bool | None = None

    def set_welcome_message_played(self, played: bool) -> None:
        """Record the state instead of touching a websocket."""
        self.played = played


class FakeSequenceGate:
    """Conformer to `SequenceGatePort` over an explicit valid-id set."""

    def __init__(self, valid: set[int]) -> None:
        self.valid = valid

    def is_sequence_id_in_current_ids(self, sequence_id: int) -> bool:
        """Membership, not truthiness: -1 style quirks stay the caller's business."""
        return sequence_id in self.valid


class FakeBrain:
    """Conformer to `AgentBrainPort`: a deterministic two-chunk stream."""

    async def generate(self, history, /, *, synthesize=False, meta_info=None):
        """Yield two chunks so the round-trip test asserts concrete content."""
        yield {"content": "hello", "synthesize": synthesize}
        yield {"content": "world", "meta_info": meta_info}

    async def check_for_completion(self, messages, check_for_completion_prompt, meta_info=None):
        """Answer the legacy (hangup_dict, metadata) judge shape."""
        return {"hangup": "No"}, {"latency_ms": 12.5}


class FakeGraphBrain(FakeBrain):
    """Conformer to `GraphBrainPort`: the graph extension surface over the base brain."""

    def __init__(self) -> None:
        self.context_data: dict = {"recipient_data": {}}
        self.current_node_entry_index: int = 0
        self._event_triggered_generation: bool = False
        self.committed = 0

    def process_event(self, event):
        """Merge-and-answer, concrete."""
        self.context_data["_last_event"] = event
        return {"transitioned": False}

    def get_node_by_id(self, node_id):
        """One known node, else None."""
        return {"id": node_id} if node_id == "node-1" else None

    def update_current_node(self) -> None:
        """Count commits so the round trip asserts a concrete value."""
        self.committed += 1

    def mark_first_response_delivered(self) -> None:
        """No-op warm-up gate."""


class NotAPort:
    """Deliberately implements none of the port members."""


# --- Legacy-class conformance (the six classes the B0 gate names) -----------------------


def test_transcriber_pool_conforms_to_the_transcription_ports():
    """The pool satisfies both the duck-typed single surface and the pool surface."""
    pool = _transcriber_pool()

    assert isinstance(pool, TranscriptionPort)
    assert isinstance(pool, TranscriptionPoolPort)


def test_synthesizer_pool_conforms_to_the_synthesis_ports():
    """The pool satisfies both the duck-typed single surface and the pool surface."""
    pool = _synthesizer_pool()

    assert isinstance(pool, SynthesisPort)
    assert isinstance(pool, SynthesisPoolPort)


def test_default_input_handler_conforms_to_the_input_port():
    """The browser-leg input handler carries the full input surface."""
    handler = DefaultInputHandler()

    assert isinstance(handler, CallInputPort)


def test_telephony_output_handler_conforms_to_the_output_port():
    """The telephony output handler carries the full output surface, latch included."""
    handler = TelephonyOutputHandler(io_provider="default", websocket=None, mark_event_meta_data=None)

    assert isinstance(handler, CallOutputPort)


def test_mark_event_meta_data_conforms_to_the_ledger_port():
    """The real mark ledger (pure in-memory) satisfies the ledger port."""
    ledger = MarkEventMetaData()

    assert isinstance(ledger, MarkLedgerPort)


def test_base_s2s_provider_surface_conforms_via_a_no_op_subclass():
    """The inherited BaseS2SProvider surface satisfies `S2SPort` (typed + runtime)."""
    provider: S2SPort = _stub_s2s_provider()

    assert isinstance(provider, S2SPort)
    assert provider.input_sample_rate == 16000
    assert provider.output_sample_rate == 24000


def test_task_manager_class_satisfies_the_sequence_gate():
    """`SequenceGatePort` is method-only, so issubclass works — TaskManager conforms."""
    assert issubclass(TaskManager, SequenceGatePort)


def test_unrelated_object_conforms_to_no_port():
    """`runtime_checkable` must actually discriminate, not accept everything."""
    stranger = NotAPort()

    for port in (
        TranscriptionPort,
        TranscriptionPoolPort,
        ActiveTranscriberProbePort,
        SynthesisPort,
        SynthesisPoolPort,
        SequenceGatePort,
        CallInputPort,
        CallOutputPort,
        WelcomeStateSetterPort,
        MarkLedgerPort,
        LlmPort,
        AgentBrainPort,
        GraphBrainPort,
        S2SPort,
    ):
        assert not isinstance(stranger, port), port.__name__


def test_ports_are_not_instantiable():
    """A protocol is a contract, not a class anyone constructs."""
    with pytest.raises(TypeError):
        TranscriptionPoolPort()  # type: ignore[misc]  # the point: instantiation must raise
    with pytest.raises(TypeError):
        MarkLedgerPort()  # type: ignore[misc]  # the point: instantiation must raise


def test_port_member_sets_are_pinned():
    """Renaming or moving a port member must be loud: spec 0004 names exactly these."""
    assert TRANSCRIPTION_POOL_METHODS <= frozenset(dir(TranscriptionPoolPort))
    assert SYNTHESIS_POOL_METHODS <= frozenset(dir(SynthesisPoolPort))
    assert INPUT_PORT_METHODS <= frozenset(dir(CallInputPort))
    assert OUTPUT_PORT_METHODS <= frozenset(dir(CallOutputPort))
    assert MARK_LEDGER_METHODS <= frozenset(dir(MarkLedgerPort))
    assert S2S_PORT_METHODS <= frozenset(dir(S2SPort))
    assert PROBE_PORT_MEMBERS <= frozenset(dir(ActiveTranscriberProbePort))
    assert not TRANSCRIPTION_POOL_METHODS & frozenset(dir(NotAPort))


# --- Step-B2 ports: fakes AND (since B2 landed) the legacy classes conform --------------


def test_probe_port_fake_conforms_with_concrete_values():
    """The three lifted leaks, answered concretely (never Mock truthiness — risk R1)."""
    probe: ActiveTranscriberProbePort = FakeActiveTranscriberProbe()

    assert isinstance(probe, ActiveTranscriberProbePort)
    assert probe.current_turn_id == 3
    assert probe.eager_eot_threshold == 0.7
    assert probe.supports_regen_settle() is True


def test_transcriber_pool_carries_the_probe_surface():
    """B2 landed: the pool conforms to the probe port (flips the honest B0 pin)."""
    pool = _transcriber_pool()

    assert isinstance(pool, ActiveTranscriberProbePort)


def test_pool_probe_members_delegate_to_the_active_inner_transcriber():
    """The lifted members read the ACTIVE inner transcriber, concretely (risk R1)."""
    inner = SimpleNamespace(connection_time=None, turn_latencies=[], current_turn_id=7, eager_eot_threshold=0.4)
    pool = TranscriberPool({ACTIVE_LABEL: inner}, None, None, ACTIVE_LABEL, {})

    assert pool.current_turn_id == 7
    assert pool.eager_eot_threshold == 0.4


def test_pool_probe_members_answer_none_when_the_inner_lacks_them():
    """A provider without the members answers None — exactly what the old digs got."""
    pool = _transcriber_pool()  # the standby component defines neither member

    assert pool.current_turn_id is None
    assert pool.eager_eot_threshold is None


def test_pool_supports_regen_settle_follows_the_active_label():
    """The capability mirrors the name-prefix exclusion, tracking the active label."""

    class DeepgramStub:
        """Name-prefix matters: the exclusion check lowercases the class name."""

    class SonioxStub:
        """Any non-excluded provider name arms the settle window."""

    pool = TranscriberPool({"hi": DeepgramStub(), "en": SonioxStub()}, None, None, "hi", {})

    assert pool.supports_regen_settle() is False
    pool.active_label = "en"
    assert pool.supports_regen_settle() is True


def test_welcome_setter_fake_conforms_and_records_state():
    """The B2 setter contract, driven through the protocol type."""
    setter: WelcomeStateSetterPort = FakeWelcomeSetter()

    assert isinstance(setter, WelcomeStateSetterPort)
    setter.set_welcome_message_played(True)
    assert cast(FakeWelcomeSetter, setter).played is True


def test_input_handlers_carry_the_welcome_setter():
    """B2 landed: both input-handler legs conform to the setter port."""
    assert isinstance(DefaultInputHandler(), WelcomeStateSetterPort)
    assert isinstance(TelephonyInputHandler(queues=None), WelcomeStateSetterPort)


def test_set_welcome_message_played_keeps_the_attribute_in_sync():
    """The setter and the raw flag (read by welcome_message_played) cannot drift."""
    handler = DefaultInputHandler()
    assert handler.welcome_message_played() is False

    handler.set_welcome_message_played(True)
    assert handler.is_welcome_message_played is True
    assert handler.welcome_message_played() is True

    handler.set_welcome_message_played(False)
    assert handler.is_welcome_message_played is False
    assert handler.welcome_message_played() is False


def test_base_synthesizer_prefers_the_injected_sequence_gate():
    """B2: with both seams wired, the typed gate decides; the backref is not consulted."""
    synth = BaseSynthesizer(task_manager_instance=FakeSequenceGate({9}), sequence_gate=FakeSequenceGate({7}))

    assert synth.should_synthesize_response(7) is True
    assert synth.should_synthesize_response(9) is False


def test_base_synthesizer_falls_back_to_the_task_manager_backref():
    """Without a gate the legacy backref keeps answering (it flows until B13c)."""
    synth = BaseSynthesizer(task_manager_instance=FakeSequenceGate({9}))

    assert synth.sequence_gate is None
    assert synth.should_synthesize_response(9) is True
    assert synth.should_synthesize_response(7) is False


def test_sequence_gate_round_trip_through_the_protocol_type():
    """Concrete valid/invalid answers through the port, including the -1 quirk id."""
    gate: SequenceGatePort = FakeSequenceGate({4, SEQUENCE_ID_ALWAYS_SEND})

    assert gate.is_sequence_id_in_current_ids(4) is True
    assert gate.is_sequence_id_in_current_ids(5) is False
    assert gate.is_sequence_id_in_current_ids(SEQUENCE_ID_ALWAYS_SEND) is True


# --- Brain ports: structural fakes prove the seam is drivable ---------------------------


async def test_brain_fake_round_trip_through_the_protocol_type():
    """Generation streams and the judge answers, all typed against the port."""
    brain: AgentBrainPort = FakeBrain()
    assert isinstance(brain, LlmPort)
    assert isinstance(brain, AgentBrainPort)

    chunks = [chunk async for chunk in brain.generate([{"role": "user", "content": "hi"}], synthesize=True)]
    assert [c["content"] for c in chunks] == ["hello", "world"]
    assert chunks[0]["synthesize"] is True

    answer, metadata = await brain.check_for_completion([], "should we hang up?")
    assert answer == {"hangup": "No"}
    assert metadata == {"latency_ms": 12.5}


def test_graph_brain_fake_conforms_to_the_extension_port():
    """The graph extension surface stacks on the base brain and stays concrete."""
    graph: GraphBrainPort = FakeGraphBrain()

    assert isinstance(graph, GraphBrainPort)
    assert isinstance(graph, AgentBrainPort)
    assert graph.get_node_by_id("node-1") == {"id": "node-1"}
    assert graph.get_node_by_id("nope") is None
    assert graph.process_event({"kind": "payment"}) == {"transitioned": False}
    assert graph.context_data["_last_event"] == {"kind": "payment"}
    graph.update_current_node()
    assert cast(FakeGraphBrain, graph).committed == 1
    assert graph._event_triggered_generation is False


# --- Module definition: empty router + the B4 service binding ---------------------------


def test_module_is_a_frozen_module_def_named_voice():
    """The registry entry is composition data with the module's own name."""
    assert isinstance(voice.MODULE, ModuleDef)
    assert voice.MODULE.name == MODULE_NAME

    with pytest.raises(dataclasses.FrozenInstanceError):
        voice.MODULE.name = "renamed"  # type: ignore[misc]  # the point: assignment must raise


def test_voice_module_is_registered_in_all_modules():
    """Spec 0001 registry pattern: the module rides `ALL_MODULES` from day one (B0)."""
    assert voice.MODULE in ALL_MODULES


def test_router_mounts_no_routes_yet():
    """An empty router changes nothing observable; the WS controller arrives in B14."""
    assert voice.MODULE.router.routes == []


def test_register_binds_exactly_the_voice_call_service():
    """B4 seam: `register` binds `VoiceCallService` and nothing else (B13a adds the rest).

    Rewrite of the B0 no-op pin (`test_register_is_a_noop_until_b13a`), 1<->1: the
    planned binding landed, so the pin flips the same way the B2 probe-surface pin did.
    """
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    spy = SimpleNamespace(register=lambda *args, **kwargs: calls.append((args, kwargs)))

    voice.MODULE.register(cast("Container", spy))

    assert [args[0] for args, _kwargs in calls] == [voice.VoiceCallService]


def test_public_surface_exports_the_ports():
    """Everything downstream steps consume must ride `__all__` (one import surface)."""
    for name in ("TranscriptionPoolPort", "SynthesisPoolPort", "SequenceGatePort", "S2SPort", "MODULE"):
        assert name in voice.__all__, name


# --- Constants: pinned against the legacy literals they mirror --------------------------


def test_constants_pin_the_legacy_literals():
    """The mirrored literals must match the legacy values byte for byte (spec 0004)."""
    assert CATEGORY_IS_USER_ONLINE == legacy_constants.IS_USER_ONLINE_MESSAGE
    assert SEQUENCE_ID_ALWAYS_SEND == -1
    assert LegacyTranscriberError("x").component == COMPONENT_TRANSCRIBER
    assert LegacySynthesizerError("x").component == COMPONENT_SYNTHESIZER


# --- Errors and guards ------------------------------------------------------------------


def test_component_errors_carry_the_attribution_triple():
    """run()'s attribution reads component/provider/model — the new hierarchy keeps them."""
    error = TranscriptionError("connect failed", provider="deepgram", model="nova-2")

    assert error.component == COMPONENT_TRANSCRIBER
    assert error.provider == "deepgram"
    assert error.model == "nova-2"
    assert isinstance(error, VoiceError)
    assert SynthesisError("x").component == COMPONENT_SYNTHESIZER


def test_ensure_label_known_returns_or_raises_the_module_error():
    """The guard narrows the label or raises the module error with client-safe details."""
    assert ensure_label_known("english", ["english", "hindi"]) == "english"

    with pytest.raises(UnknownComponentLabelError) as exc_info:
        ensure_label_known("french", ["english", "hindi"])
    assert exc_info.value.details == {"label": "french", "available": ["english", "hindi"]}


# --- Typed views: pinned against the real legacy builders -------------------------------


def test_turn_meta_view_reads_the_four_id_spaces():
    """The helper lifts exactly the correlation ids, tolerating the None-meta quirk."""
    meta = turn_meta_from_meta_info(
        {"sequence_id": 7, "turn_id": 2, "response_uid": "r-1", "response_group_uid": "g-1", "asr_turn_id": "turn_3"}
    )

    assert (meta.sequence_id, meta.turn_id) == (7, 2)
    assert (meta.response_uid, meta.response_group_uid) == ("r-1", "g-1")
    assert meta.asr_turn_id == "turn_3"
    assert turn_meta_from_meta_info(None).sequence_id is None


def test_packet_view_matches_the_legacy_packet_constructor():
    """`create_ws_data_packet` output reads cleanly into the typed view, quirks intact."""
    packet = create_ws_data_packet(data=b"\x00\x01", meta_info={"sequence_id": 3, "type": "audio"})

    view = packet_view(packet)

    assert view.data == b"\x00\x01"
    assert view.meta_info is not None
    assert view.meta_info["sequence_id"] == 3
    # The legacy constructor injects these two flags into every non-None meta dict.
    assert view.meta_info["is_md5_hash"] is False
    assert view.meta_info["llm_generated"] is False


def test_transcriber_event_view_normalises_both_wire_shapes():
    """Control strings and transcript dicts fold into the one typed event."""
    control = transcriber_event_view("speech_started")
    transcript = transcriber_event_view({"type": "transcript", "content": "hello there"})

    assert (control.type, control.content) == ("speech_started", None)
    assert (transcript.type, transcript.content) == ("transcript", "hello there")


def test_lid_decision_record_matches_the_real_builder():
    """The typed view and `build_lid_decision_record` cannot drift apart silently."""
    record = build_lid_decision_record(
        outcome="stay",
        fired_at=100.0,
        now=100.25,
        active_transcript="hola",
        active="english",
        detector_transcript="hola amigo",
        detector_lang_tag="es",
        decision={"detected_language": "es", "detection_confidence": 0.9, "reasoning": " keep going "},
        buffered_max_segment_s=1.23456,
        speculation_started=False,
    )

    view = LidDecisionRecord(**record)

    assert set(record) == set(LidDecisionRecord.model_fields)
    assert view.flow == LID_FLOW_LLM_SWITCH
    assert view.path == LID_PATH_TURN_BOUNDARY
    assert view.decide_latency_ms == 250.0
    assert view.detected_language == "es"
    assert view.reasoning == "keep going"
    assert view.buffered_max_segment_s == 1.235
    assert view.switched_to is None
    assert view.context_note_sent_at is None


def test_latency_report_and_hangup_detail_accept_the_teardown_shape():
    """The report views parse the snapshot keys task_manager emits at teardown."""
    report = LatencyReport.model_validate(
        {
            "llm_latencies": {"connection_latency_ms": None, "turn_latencies": [], "other_latencies": []},
            "welcome_message_sent_ts": None,
            "stream_sid_ts": None,
            "user_bot_latencies": [{"sequence_id": 1, "agent_start_ms": 120.5, "latency_ms": 80.0}],
        }
    )

    assert report.user_bot_latencies[0].sequence_id == 1
    assert report.user_bot_latencies[0].agent_end_ms is None
    assert HangupDetail().reason is None
    assert HangupDetail(reason=HangupReason.END_CALL_TOOL).ended_by_assistant is False


def test_utils_mint_uuid4_mark_ids_and_read_the_wall_clock():
    """Mark ids stay uuid4 strings; the clock is plain epoch seconds."""
    mark_id = new_mark_id()
    assert uuid.UUID(mark_id).version == 4
    assert new_mark_id() != mark_id

    before = time.time()
    now = epoch_seconds()
    assert before <= now <= time.time()
