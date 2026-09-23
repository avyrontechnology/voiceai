"""The moved pure functions (spec 0004, B3): behavior at the new home, delegators pinned.

Two contracts under test. First, the six functions moved verbatim out of
task_manager.py 124-272 behave concretely when driven through their NEW module path
(`voiceai.modules.voice.static_methods`). Second — the migration's load-bearing half —
``voiceai.agent_manager.task_manager`` binds the SAME function objects under the same
names, so every legacy import keeps resolving, every monkeypatch string path keeps
landing on the lookup site, and the ``lru_cache`` on `welcome_pcm_upsampled` stays ONE
shared cache. A subprocess canary proves the move dragged no provider stack into the
module's import graph (the adapters package surface stays light).
"""

from __future__ import annotations

import base64
import struct
import subprocess
import sys
from pathlib import Path

import voiceai.agent_manager.task_manager as legacy_tm
import voiceai.constants as legacy_constants
from voiceai.enums import ToolScope
from voiceai.helpers.utils import resample as legacy_resample
from voiceai.modules.voice import adapters
from voiceai.modules.voice.constants import LID_FLOW_LLM_SWITCH, LID_PATH_IDLE_FLUSH, LID_PATH_TURN_BOUNDARY
from voiceai.modules.voice.static_methods import (
    _inject_end_call_tool,
    asr_id_to_int,
    build_lid_decision_record,
    is_alphanumeric_readout,
    trailing_utterance_text,
    welcome_pcm_upsampled,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SUBPROCESS_TIMEOUT_S = 120

#: Provider stacks the pure-function module must never load (its only legacy values —
#: resample and the end-call tool constants — ride the adapters package surface, which
#: imports helpers/constants alone). `voiceai.llms` is absent from this list on purpose:
#: it is a declared §3.1 transitional allowance and the modules registry already loads it.
FORBIDDEN_PROVIDER_MODULES = (
    "voiceai.transcriber",
    "voiceai.synthesizer",
    "voiceai.input_handlers",
    "voiceai.output_handlers",
    "voiceai.s2s",
    "voiceai.providers",
    "voiceai.agent_manager",
)

CANARY_CODE = f"""
import sys
import voiceai.modules.voice.static_methods
loaded = [m for m in {FORBIDDEN_PROVIDER_MODULES!r} if m in sys.modules]
assert not loaded, f"provider stacks imported by static_methods: {{loaded}}"
"""

MOVED_NAMES = (
    "_inject_end_call_tool",
    "asr_id_to_int",
    "build_lid_decision_record",
    "is_alphanumeric_readout",
    "trailing_utterance_text",
    "welcome_pcm_upsampled",
)


def _pcm_b64(num_samples: int, amplitude: int = 2000) -> str:
    """Deterministic little-endian PCM-16 test tone, base64 encoded."""
    pcm = b"".join(struct.pack("<h", (amplitude if (i // 3) % 2 else -amplitude)) for i in range(num_samples))
    return base64.b64encode(pcm).decode()


# --- Delegators: task_manager binds the SAME objects (lookups/patches keep resolving) ---


def test_task_manager_binds_the_same_function_objects():
    """Identity, not equivalence: the tm names ARE the moved functions (R3 patch safety)."""
    import voiceai.modules.voice.static_methods as static_methods

    for name in MOVED_NAMES:
        assert getattr(legacy_tm, name) is getattr(static_methods, name), name


def test_welcome_cache_is_one_shared_lru():
    """The memoized object moved whole: one cache serves both import paths."""
    assert legacy_tm.welcome_pcm_upsampled.cache_info().maxsize == 256

    welcome = _pcm_b64(64, amplitude=1234)
    first = welcome_pcm_upsampled(welcome, 24000, 8000)
    hits_before = legacy_tm.welcome_pcm_upsampled.cache_info().hits
    second = legacy_tm.welcome_pcm_upsampled(welcome, 24000, 8000)

    assert first == second
    assert legacy_tm.welcome_pcm_upsampled.cache_info().hits == hits_before + 1


def test_static_methods_import_no_provider_stack():
    """Subprocess canary: a clean interpreter loads the module without any provider stack."""
    result = subprocess.run(
        [sys.executable, "-c", CANARY_CODE],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert result.returncode == 0, f"static_methods canary failed:\n{result.stderr}"


def test_adapter_surface_reexports_the_legacy_values_by_identity():
    """The bodies read the SAME legacy objects as before the move (behavior invariance)."""
    assert adapters.resample is legacy_resample
    assert adapters.END_CALL_FUNCTION_PREFIX is legacy_constants.END_CALL_FUNCTION_PREFIX
    assert adapters.END_CALL_TOOL_DEFINITION is legacy_constants.END_CALL_TOOL_DEFINITION


# --- Behavior at the new home (the legacy suites keep driving the tm bindings) ----------


def test_welcome_pcm_upsampled_resamples_and_passes_through():
    """8k→24k triples the sample count; source==target answers the exact input bytes."""
    welcome = _pcm_b64(800)

    upsampled = welcome_pcm_upsampled(welcome, 24000, 8000)
    untouched = welcome_pcm_upsampled(welcome, 8000, 8000)

    assert abs(len(upsampled) - 800 * 2 * 3) <= 0.02 * 800 * 2 * 3
    assert untouched == base64.b64decode(welcome)


def test_inject_end_call_tool_builds_the_tool_and_params():
    """A None config grows the tool list plus the scope/nodes params entry."""
    api_tools = _inject_end_call_tool(None, scope=ToolScope.NODE, nodes=["goodbye"], description="say bye")

    assert api_tools["tools"][-1]["function"]["name"] == "end_call"
    assert api_tools["tools"][-1]["function"]["description"] == "say bye"
    params = api_tools["tools_params"][adapters.END_CALL_FUNCTION_PREFIX]
    assert params == {"pre_call_message": None, "scope": ToolScope.NODE.value, "nodes": ["goodbye"]}
    # The injected definition is a deepcopy: the shared legacy constant stayed pristine.
    assert adapters.END_CALL_TOOL_DEFINITION["function"]["description"] != "say bye"


def test_inject_end_call_tool_is_idempotent():
    """A second injection is a no-op — the first params entry wins (legacy dedup)."""
    api_tools = _inject_end_call_tool(None, scope=ToolScope.GLOBAL, nodes=[])
    again = _inject_end_call_tool(api_tools, scope=ToolScope.NODE, nodes=["z"])

    assert again is api_tools
    assert len(again["tools"]) == 1
    assert again["tools_params"][adapters.END_CALL_FUNCTION_PREFIX]["scope"] == ToolScope.GLOBAL.value


def test_is_alphanumeric_readout_discriminates():
    """Digit-bearing tokens with ≤2 plain tokens count as a readout; prose does not."""
    assert is_alphanumeric_readout("PNR 4X9B2 hai") is True
    assert is_alphanumeric_readout("what is the fee for this course") is False
    assert is_alphanumeric_readout("") is False


def test_asr_id_to_int_coerces_the_two_wire_shapes():
    """Deepgram ints pass through; OpenAI turn_N strings coerce; junk answers None."""
    assert asr_id_to_int(5) == 5
    assert asr_id_to_int("turn_12") == 12
    assert asr_id_to_int("turn_") is None
    assert asr_id_to_int(None) is None


def test_trailing_utterance_text_cuts_at_gap_and_language():
    """Segments join back to a silence boundary; a language flip ends the utterance."""
    segments = [
        {"lang": "hi", "ts": 1.0, "audio_s": 1.0, "text": "namaste"},
        {"lang": "en", "ts": 10.0, "audio_s": 1.0, "text": "what is"},
        {"lang": "en", "ts": 11.5, "audio_s": 1.0, "text": "the fee"},
    ]

    assert trailing_utterance_text(segments) == "what is the fee"
    assert trailing_utterance_text([]) == ""
    assert trailing_utterance_text(None) == ""


def _lid_record(active_transcript):
    """One builder firing with fixed timings; only the path discriminator varies."""
    return build_lid_decision_record(
        outcome="stay",
        active_transcript=active_transcript,
        fired_at=10.0,
        now=10.1,
        active="english",
        detector_transcript="hola",
        detector_lang_tag="es",
        decision=None,
        buffered_max_segment_s=0.5,
        speculation_started=False,
    )


def test_build_lid_decision_record_stamps_flow_and_path():
    """The moved builder keeps the persisted discriminators the constants pin."""
    boundary = _lid_record("hola")
    idle = _lid_record(None)

    assert boundary["flow"] == LID_FLOW_LLM_SWITCH
    assert boundary["path"] == LID_PATH_TURN_BOUNDARY
    assert idle["path"] == LID_PATH_IDLE_FLUSH
    assert boundary["decide_latency_ms"] == 100.0
