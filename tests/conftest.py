"""Fixtures shared by more than one test module."""

import os

# Must precede any import that pulls in litellm: it otherwise fetches its model-price map over
# the network at import time, so the suite would not run offline.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# The OpenAI SDK refuses to build a client without a key, and the simple_llm_agent path has no
# kwarg to pass one. Overwritten rather than defaulted, so a developer's .env can never point a
# test at a live account. Tests needing any other provider credential supply it themselves.
os.environ["OPENAI_API_KEY"] = "test-key"

import socket  # noqa: E402
from functools import partial

import pytest  # noqa: E402
from unittest.mock import AsyncMock, MagicMock

from voiceai.modules.voice.session.language import LanguageSwitchCoordinator
from voiceai.modules.voice.session.language import lid_gate as _voice_lid_gate
from voiceai.modules.voice.session.language import switcher as _voice_switcher
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.transcriber.transcriber_pool import TranscriberPool

# --- spec 0002 A0: outbound-socket block ---------------------------------------------------
# The suite is offline-only. Any un-mocked outbound socket connect fails loudly with the
# offending test's id, so a dead-namespace string patch can never silently make live IO.
# Unix sockets and loopback destinations stay open: pytest/asyncio plumbing and local test
# servers depend on them. Opt out per-test with @pytest.mark.allow_network.

ALLOW_NETWORK_MARKER = "allow_network"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "::", "0.0.0.0", ""})
_LOOPBACK_PREFIX = "127."


def pytest_configure(config):
    """Register the socket-guard opt-out marker (addopts pins --strict-markers)."""
    config.addinivalue_line(
        "markers",
        f"{ALLOW_NETWORK_MARKER}: opt out of the autouse outbound-socket block (justify with a TODO)",
    )


def _is_local_destination(sock, address):
    """Return True when a connect() stays on this machine (unix socket or loopback host)."""
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    host = host.strip("[]").lower()
    return host in _LOOPBACK_HOSTS or host.startswith(_LOOPBACK_PREFIX)


@pytest.fixture(autouse=True)
def block_outbound_sockets(request):
    """Fail any un-mocked outbound socket connect, naming the test (spec 0002 A0)."""
    if request.node.get_closest_marker(ALLOW_NETWORK_MARKER) is not None:
        yield
        return
    own_attribute = socket.socket.__dict__.get("connect")
    real_connect = socket.socket.connect
    node_id = request.node.nodeid

    def _guarded_connect(self, address):
        if _is_local_destination(self, address):
            return real_connect(self, address)
        raise RuntimeError(
            f"Outbound socket blocked in the offline suite: {node_id} tried to connect to "
            f"{address!r}. Mock the network client (a dead monkeypatch target no longer "
            f"intercepts this call), or opt out with @pytest.mark.{ALLOW_NETWORK_MARKER}."
        )

    socket.socket.connect = _guarded_connect
    try:
        yield
    finally:
        if own_attribute is None:
            del socket.socket.connect
        else:
            socket.socket.connect = own_attribute

_SWITCH_DECISION = {"target_language": "mr", "target_confidence": 0.95, "reasoning": "clear Marathi"}


@pytest.fixture
def language_switch_tm(monkeypatch):
    """Build a LanguageSwitchCoordinator over a fake session (spec 0004 B9a port).

    The session double (``coordinator.session``) is the same MagicMock the fixture
    always built; the coordinator drives the REAL moved bodies at their new home
    (`voiceai.modules.voice.session.language`), and the five real private helpers
    the old fixture re-bound off TaskManager — the three switch tunables,
    ``record_lid_event`` and the ``detector_corroborates`` static — are re-bound
    onto the double from those same moved functions, so internal mangled dispatch
    keeps hitting the real bodies.
    """

    def _build(gap=0.0, audio_playing=True):
        monkeypatch.setenv("LANGUAGE_SWITCH_SETTLE_MS", "0")  # skip the detector-tail settle
        tm = MagicMock()
        tm.task_config = {
            "tools_config": {
                "llm_agent": {"agent_type": "graph_agent"},  # suppress speculation
                "language_switch_audio_gap_s": gap,
            }
        }
        tm.language = "hi"
        tm.conversation_ended = False
        tm.hangup_triggered = False
        tm.function_call_in_flight = False
        tm.multilingual_prompts = {"hi": "p", "mr": "p"}
        tm._should_ignore_transcriber_input = MagicMock(return_value=False)

        pool = MagicMock(spec=TranscriberPool)
        pool.labels = ["hi", "mr"]
        pool.lid_detection_events = []
        pool.lid_buffer_max_segment_seconds.return_value = 2.0
        pool.lid_buffer_language_confidence.return_value = 0.9
        # Corroboration is per-segment: prob and duration must describe the same utterance.
        pool.lid_buffer_segments.return_value = [{"lang": "mr", "prob": 0.9, "audio_s": 2.0}]
        pool.take_lid_transcript.return_value = ("mala samajla nahi", "mr")
        synth = MagicMock(spec=SynthesizerPool)
        synth.labels = ["hi", "mr"]
        tm.tools = {"transcriber": pool, "synthesizer": synth, "input": MagicMock()}

        tm.language_switcher = MagicMock()
        tm.language_switcher.explicit_only = False
        tm.language_switcher.decide = AsyncMock(return_value=_SWITCH_DECISION)
        tm._inflight_response_activity = MagicMock(
            return_value={"audio_playing": audio_playing, "response_in_pipeline": True}
        )
        tm._TaskManager__cleanup_downstream_tasks = AsyncMock()
        tm.switch_language = AsyncMock()
        tm._TaskManager__language_directive = MagicMock(return_value="note")
        tm._TaskManager__play_switch_handoff = AsyncMock()
        tm._TaskManager__prepare_followup_generation = MagicMock(return_value=None)
        tm.conversation_history = MagicMock()
        tm.conversation_history.replace_last_user.return_value = True
        for name in ("switch_audio_gap_s", "switch_settle_ms", "switch_decide_timeout_s"):
            setattr(tm, f"_TaskManager__{name}", partial(getattr(_voice_switcher, name), tm))
        tm._TaskManager__record_lid_event = partial(_voice_lid_gate.record_lid_event, tm)
        tm._TaskManager__detector_corroborates = _voice_lid_gate.detector_corroborates
        return LanguageSwitchCoordinator(tm)

    return _build
