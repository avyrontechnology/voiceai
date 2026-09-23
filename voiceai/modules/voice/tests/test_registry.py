"""The provider registry vs the ``voiceai.providers`` shim (spec 0004, B3, risk R6-style).

``RECORDED_PROVIDERS_STAR_SURFACE`` is the measured public-name surface of the legacy
``voiceai/providers.py`` right before B3 turned it into a shim (62 names, 2026-09-17):
every name its star-import consumers (task_manager.py:63, ``voiceai/models.py``) picked
up. The registry must keep exporting EXACTLY this set — a dropped name silently breaks
a star importer, an added name silently grows a frozen legacy surface — and the shim
must resolve each name to the registry's object by IDENTITY, so in-place monkeypatching
of a map by any legacy test keeps hitting the one real registry.
"""

from __future__ import annotations

import voiceai.enums as legacy_enums
import voiceai.providers as providers_shim
from voiceai.modules.voice import registry

# Recorded pre-shim snapshot — do not edit except through a spec.
RECORDED_PROVIDERS_STAR_SURFACE = frozenset(
    {
        "AssemblyAITranscriber",
        "AzureLLM",
        "AzureSynthesizer",
        "AzureTranscriber",
        "CartesiaSynthesizer",
        "DeepgramSynthesizer",
        "DeepgramTranscriber",
        "DefaultInputHandler",
        "DefaultOutputHandler",
        "ElevenLabsTranscriber",
        "ElevenlabsSynthesizer",
        "ElevenlabsV3Synthesizer",
        "ExotelInputHandler",
        "ExotelOutputHandler",
        "FreeSwitchInputHandler",
        "FreeSwitchOutputHandler",
        "GeminiLLM",
        "GeminiLiveS2S",
        "GeminiTranscriber",
        "GladiaTranscriber",
        "GoogleTranscriber",
        "KalpaSynthesizer",
        "LLMProvider",
        "LiteLLM",
        "MayaSynthesizer",
        "OPENAISynthesizer",
        "OpenAIRealtimeS2S",
        "OpenAITranscriber",
        "OpenAiLLM",
        "PixaSynthesizer",
        "PixaTranscriber",
        "PlivoInputHandler",
        "PlivoOutputHandler",
        "PollySynthesizer",
        "RimeSynthesizer",
        "S2SProvider",
        "SUPPORTED_INPUT_HANDLERS",
        "SUPPORTED_INPUT_TELEPHONY_HANDLERS",
        "SUPPORTED_LLM_PROVIDERS",
        "SUPPORTED_OUTPUT_HANDLERS",
        "SUPPORTED_OUTPUT_TELEPHONY_HANDLERS",
        "SUPPORTED_S2S_PROVIDERS",
        "SUPPORTED_SYNTHESIZER_MODELS",
        "SUPPORTED_TRANSCRIBER_MODELS",
        "SUPPORTED_TRANSCRIBER_PROVIDERS",
        "SarvamSynthesizer",
        "SarvamTranscriber",
        "SipTrunkInputHandler",
        "SipTrunkOutputHandler",
        "SmallestSynthesizer",
        "SmallestTranscriber",
        "SonioxTranscriber",
        "SynthesizerProvider",
        "TalkoInputHandler",
        "TalkoOutputHandler",
        "TelephonyProvider",
        "TranscriberProvider",
        "TwilioInputHandler",
        "TwilioOutputHandler",
        "VobizInputHandler",
        "VobizOutputHandler",
        "elevenlabs_synthesizer",
    }
)

#: The exact import tuple of tests/test_provider_registry_parity.py — named here so a
#: registry edit that would break the parity suite's imports fails in `make check` too.
PARITY_SUITE_IMPORT_NAMES = (
    "SUPPORTED_INPUT_HANDLERS",
    "SUPPORTED_INPUT_TELEPHONY_HANDLERS",
    "SUPPORTED_LLM_PROVIDERS",
    "SUPPORTED_OUTPUT_HANDLERS",
    "SUPPORTED_OUTPUT_TELEPHONY_HANDLERS",
    "SUPPORTED_S2S_PROVIDERS",
    "SUPPORTED_SYNTHESIZER_MODELS",
    "SUPPORTED_TRANSCRIBER_PROVIDERS",
)


def test_recorded_snapshot_has_the_measured_size():
    """The recorded pre-shim surface was exactly 62 public names."""
    assert len(RECORDED_PROVIDERS_STAR_SURFACE) == 62


def test_registry_exports_exactly_the_recorded_surface():
    """`__all__` is the frozen legacy surface: nothing dropped, nothing smuggled in."""
    assert frozenset(registry.__all__) == RECORDED_PROVIDERS_STAR_SURFACE


def test_shim_resolves_every_name_to_the_registry_object():
    """Identity across the shim: maps, classes, enums and the routing shim are shared."""
    for name in registry.__all__:
        assert getattr(providers_shim, name) is getattr(registry, name), name


def test_parity_suite_import_names_are_preserved():
    """The eight names tests/test_provider_registry_parity.py imports keep resolving."""
    for name in PARITY_SUITE_IMPORT_NAMES:
        assert name in registry.__all__, name
        assert isinstance(getattr(providers_shim, name), dict), name


def test_backwards_compat_transcriber_models_map_is_verbatim():
    """The deliberately tiny legacy map survived the move byte for byte."""
    assert registry.SUPPORTED_TRANSCRIBER_MODELS == {"deepgram": registry.DeepgramTranscriber}


def test_registry_enums_are_the_legacy_enums():
    """The five provider enums re-export from voiceai.enums by identity (§3.1 allowance)."""
    assert registry.TelephonyProvider is legacy_enums.TelephonyProvider
    assert registry.SynthesizerProvider is legacy_enums.SynthesizerProvider
    assert registry.TranscriberProvider is legacy_enums.TranscriberProvider
    assert registry.LLMProvider is legacy_enums.LLMProvider
    assert registry.S2SProvider is legacy_enums.S2SProvider


def test_task_manager_star_import_still_binds_the_registry_maps():
    """tm's `from voiceai.providers import *` (line 63) lands on the shared map objects."""
    import voiceai.agent_manager.task_manager as legacy_tm

    assert legacy_tm.SUPPORTED_OUTPUT_HANDLERS is registry.SUPPORTED_OUTPUT_HANDLERS
    assert legacy_tm.SUPPORTED_S2S_PROVIDERS is registry.SUPPORTED_S2S_PROVIDERS
    assert legacy_tm.elevenlabs_synthesizer is registry.elevenlabs_synthesizer
