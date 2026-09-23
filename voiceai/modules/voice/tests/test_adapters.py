"""The §3.1 adapters (spec 0004, B3): identity bridges and typed factories.

The adapters are the voice module's ONLY sanctioned legacy import point. Everything
they expose must be the legacy object ITSELF (identity, never a copy or subclass), so
behavior cannot fork across the seam: the provider classes, the transition error
aliases run()'s attribution catches, and the light values on the package surface.
The factories construct through the frozen registry maps and turn an unknown provider
into the module error instead of a bare ``KeyError`` — proven here with recording fakes
patched INTO the real maps (never by instantiating a network provider; the conftest
socket guard would fail that loudly).
"""

from __future__ import annotations

import pytest

import voiceai.exceptions as legacy_exceptions
from voiceai.input_handlers.default import DefaultInputHandler as LegacyDefaultInputHandler
from voiceai.modules.voice import registry
from voiceai.modules.voice.adapters import llm, s2s, synthesis, telephony, transcription
from voiceai.modules.voice.errors import UnknownComponentLabelError
from voiceai.modules.voice.ports import CallInputPort, CallOutputPort
from voiceai.output_handlers.default import DefaultOutputHandler as LegacyDefaultOutputHandler
from voiceai.synthesizer.kalpa_synthesizer import KalpaSynthesizer as LegacyKalpaSynthesizer
from voiceai.transcriber.deepgram_transcriber import DeepgramTranscriber as LegacyDeepgramTranscriber

DEFAULT_PROVIDER = "default"
FAKE_PROVIDER_KEY = "b3-test-fake"
UNKNOWN_PROVIDER = "no-such-provider"


class RecordingProvider:
    """Stands in for a legacy provider class: records the constructor kwargs."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


# --- Identity: the adapter names ARE the legacy objects ---------------------------------


def test_adapter_classes_are_the_legacy_classes():
    """No copies, no subclasses: one class object per provider across the seam."""
    assert transcription.DeepgramTranscriber is LegacyDeepgramTranscriber
    assert synthesis.KalpaSynthesizer is LegacyKalpaSynthesizer
    assert telephony.DefaultInputHandler is LegacyDefaultInputHandler
    assert telephony.DefaultOutputHandler is LegacyDefaultOutputHandler


def test_error_aliases_are_the_legacy_error_classes():
    """The transition aliases the errors-module TODO names: run() keeps catching them."""
    assert transcription.TranscriberError is legacy_exceptions.TranscriberError
    assert synthesis.SynthesizerError is legacy_exceptions.SynthesizerError
    assert llm.LLMError is legacy_exceptions.LLMError
    assert s2s.VoiceAIComponentError is legacy_exceptions.VoiceAIComponentError
    # Raising through the alias attributes exactly as the legacy hierarchy does.
    assert transcription.TranscriberError("x", provider="deepgram").component == "transcriber"


def test_registry_maps_route_elevenlabs_through_the_moved_shim():
    """The map's "elevenlabs" entry is the model-routing function, not a class."""
    assert registry.SUPPORTED_SYNTHESIZER_MODELS["elevenlabs"] is synthesis.elevenlabs_synthesizer


# --- Factories: construction through the frozen maps ------------------------------------


def test_input_and_output_factories_build_the_default_leg():
    """The browser leg constructs offline and satisfies the IO ports."""
    input_handler = telephony.create_input_handler(DEFAULT_PROVIDER)
    output_handler = telephony.create_output_handler(DEFAULT_PROVIDER)

    assert isinstance(input_handler, LegacyDefaultInputHandler)
    assert isinstance(input_handler, CallInputPort)
    assert isinstance(output_handler, LegacyDefaultOutputHandler)
    assert isinstance(output_handler, CallOutputPort)


@pytest.mark.parametrize(
    ("factory", "registry_map"),
    [
        (transcription.create_transcriber, registry.SUPPORTED_TRANSCRIBER_PROVIDERS),
        (synthesis.create_synthesizer, registry.SUPPORTED_SYNTHESIZER_MODELS),
        (telephony.create_input_handler, registry.SUPPORTED_INPUT_HANDLERS),
        (telephony.create_output_handler, registry.SUPPORTED_OUTPUT_HANDLERS),
        (llm.create_llm, registry.SUPPORTED_LLM_PROVIDERS),
        (s2s.create_s2s, registry.SUPPORTED_S2S_PROVIDERS),
    ],
    ids=["transcriber", "synthesizer", "input", "output", "llm", "s2s"],
)
def test_factory_passes_kwargs_through_the_real_map(monkeypatch, factory, registry_map):
    """Each factory reads ITS live registry map and forwards kwargs untouched."""
    monkeypatch.setitem(registry_map, FAKE_PROVIDER_KEY, RecordingProvider)

    built = factory(FAKE_PROVIDER_KEY, model="m-1", stream=True)

    assert isinstance(built, RecordingProvider)
    assert built.kwargs == {"model": "m-1", "stream": True}


@pytest.mark.parametrize(
    ("factory", "known_key"),
    [
        (transcription.create_transcriber, "deepgram"),
        (synthesis.create_synthesizer, "polly"),
        (telephony.create_input_handler, DEFAULT_PROVIDER),
        (telephony.create_output_handler, DEFAULT_PROVIDER),
        (llm.create_llm, "openai"),
        (s2s.create_s2s, "openai_realtime"),
    ],
    ids=["transcriber", "synthesizer", "input", "output", "llm", "s2s"],
)
def test_factory_raises_the_module_error_for_unknown_providers(factory, known_key):
    """An unknown provider is a module error carrying client-safe identifiers only."""
    with pytest.raises(UnknownComponentLabelError) as exc_info:
        factory(UNKNOWN_PROVIDER)

    assert exc_info.value.details["label"] == UNKNOWN_PROVIDER
    assert known_key in exc_info.value.details["available"]
