"""The provider registries: ``voiceai/providers.py``'s star surface, moved verbatim (B3).

``voiceai/providers.py`` is now a tagged spec-0004 legacy shim star-re-exporting THIS
module, so everything its star-import consumers (task_manager.py:63, ``voiceai/models.py``)
ever picked up resolves here: the provider classes, the five provider enums,
`elevenlabs_synthesizer`, and the nine ``SUPPORTED_*`` maps. The maps below are the
legacy dict literals byte for byte — `tests/test_provider_registry_parity.py` pins their
key sets against the enums, and the map OBJECTS are shared with the shim (identity), so
in-place monkeypatching by legacy tests keeps hitting the one real registry.

The classes arrive through ``adapters/`` (§3.1 bridge 1 — the only voice files that may
import the legacy provider stacks); this file itself imports no legacy code beyond the
declared ``voiceai.enums`` transitional allowance. The adapters' factories read these
maps back at call time, which keeps the adapter/registry pair acyclic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from voiceai.enums import LLMProvider, S2SProvider, SynthesizerProvider, TelephonyProvider, TranscriberProvider
from voiceai.modules.voice.adapters.llm import AzureLLM, GeminiLLM, LiteLLM, OpenAiLLM
from voiceai.modules.voice.adapters.s2s import GeminiLiveS2S, OpenAIRealtimeS2S
from voiceai.modules.voice.adapters.synthesis import (
    AzureSynthesizer,
    CartesiaSynthesizer,
    DeepgramSynthesizer,
    ElevenlabsSynthesizer,
    ElevenlabsV3Synthesizer,
    KalpaSynthesizer,
    MayaSynthesizer,
    OPENAISynthesizer,
    PixaSynthesizer,
    PollySynthesizer,
    RimeSynthesizer,
    SarvamSynthesizer,
    SmallestSynthesizer,
    elevenlabs_synthesizer,
)
from voiceai.modules.voice.adapters.telephony import (
    DefaultInputHandler,
    DefaultOutputHandler,
    ExotelInputHandler,
    ExotelOutputHandler,
    FreeSwitchInputHandler,
    FreeSwitchOutputHandler,
    PlivoInputHandler,
    PlivoOutputHandler,
    SipTrunkInputHandler,
    SipTrunkOutputHandler,
    TalkoInputHandler,
    TalkoOutputHandler,
    TwilioInputHandler,
    TwilioOutputHandler,
    VobizInputHandler,
    VobizOutputHandler,
)
from voiceai.modules.voice.adapters.transcription import (
    AssemblyAITranscriber,
    AzureTranscriber,
    DeepgramTranscriber,
    ElevenLabsTranscriber,
    GeminiTranscriber,
    GladiaTranscriber,
    GoogleTranscriber,
    OpenAITranscriber,
    PixaTranscriber,
    SarvamTranscriber,
    SmallestTranscriber,
    SonioxTranscriber,
)

__all__ = [
    "SUPPORTED_INPUT_HANDLERS",
    "SUPPORTED_INPUT_TELEPHONY_HANDLERS",
    "SUPPORTED_LLM_PROVIDERS",
    "SUPPORTED_OUTPUT_HANDLERS",
    "SUPPORTED_OUTPUT_TELEPHONY_HANDLERS",
    "SUPPORTED_S2S_PROVIDERS",
    "SUPPORTED_SYNTHESIZER_MODELS",
    "SUPPORTED_TRANSCRIBER_MODELS",
    "SUPPORTED_TRANSCRIBER_PROVIDERS",
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
]

#: Registry values are legacy provider constructors: classes, plus the elevenlabs
#: model-routing function — all called with the free-form legacy kwargs contract.
ProviderFactory = Callable[..., Any]  # why: the legacy constructors are untyped kwargs seams

# --- The nine maps, moved verbatim from voiceai/providers.py (spec 0004 B3) -------------

SUPPORTED_SYNTHESIZER_MODELS: Final[dict[str, ProviderFactory]] = {
    SynthesizerProvider.POLLY.value: PollySynthesizer,
    SynthesizerProvider.ELEVENLABS.value: elevenlabs_synthesizer,
    SynthesizerProvider.OPENAI.value: OPENAISynthesizer,
    SynthesizerProvider.DEEPGRAM.value: DeepgramSynthesizer,
    SynthesizerProvider.AZURETTS.value: AzureSynthesizer,
    SynthesizerProvider.CARTESIA.value: CartesiaSynthesizer,
    SynthesizerProvider.SMALLEST.value: SmallestSynthesizer,
    SynthesizerProvider.SARVAM.value: SarvamSynthesizer,
    SynthesizerProvider.RIME.value: RimeSynthesizer,
    SynthesizerProvider.PIXA.value: PixaSynthesizer,
    SynthesizerProvider.MAYA.value: MayaSynthesizer,
    SynthesizerProvider.KALPA.value: KalpaSynthesizer,
}

SUPPORTED_TRANSCRIBER_PROVIDERS: Final[dict[str, ProviderFactory]] = {
    TranscriberProvider.DEEPGRAM.value: DeepgramTranscriber,
    TranscriberProvider.AZURE.value: AzureTranscriber,
    TranscriberProvider.SARVAM.value: SarvamTranscriber,
    TranscriberProvider.ASSEMBLY.value: AssemblyAITranscriber,
    TranscriberProvider.GOOGLE.value: GoogleTranscriber,
    TranscriberProvider.PIXA.value: PixaTranscriber,
    TranscriberProvider.GLADIA.value: GladiaTranscriber,
    TranscriberProvider.ELEVENLABS.value: ElevenLabsTranscriber,
    TranscriberProvider.SMALLEST.value: SmallestTranscriber,
    TranscriberProvider.OPENAI.value: OpenAITranscriber,
    TranscriberProvider.SONIOX.value: SonioxTranscriber,
    TranscriberProvider.GEMINI.value: GeminiTranscriber,
}

# Backwards compatibility
SUPPORTED_TRANSCRIBER_MODELS: Final[dict[str, ProviderFactory]] = {"deepgram": DeepgramTranscriber}

SUPPORTED_LLM_PROVIDERS: Final[dict[str, ProviderFactory]] = {
    LLMProvider.OPENAI.value: OpenAiLLM,
    LLMProvider.COHERE.value: LiteLLM,
    LLMProvider.OLLAMA.value: LiteLLM,
    LLMProvider.DEEPINFRA.value: LiteLLM,
    LLMProvider.TOGETHER.value: LiteLLM,
    LLMProvider.FIREWORKS.value: LiteLLM,
    LLMProvider.AZURE_OPENAI.value: AzureLLM,
    LLMProvider.PERPLEXITY.value: LiteLLM,
    LLMProvider.VLLM.value: LiteLLM,
    LLMProvider.ANYSCALE.value: LiteLLM,
    LLMProvider.CUSTOM.value: OpenAiLLM,
    LLMProvider.OLA.value: OpenAiLLM,
    LLMProvider.GROQ.value: LiteLLM,
    LLMProvider.ANTHROPIC.value: LiteLLM,
    LLMProvider.DEEPSEEK.value: LiteLLM,
    LLMProvider.OPENROUTER.value: LiteLLM,
    LLMProvider.AZURE.value: AzureLLM,
    LLMProvider.GOOGLE.value: GeminiLLM,
}
SUPPORTED_INPUT_HANDLERS: Final[dict[str, ProviderFactory]] = {
    TelephonyProvider.DEFAULT.value: DefaultInputHandler,
    TelephonyProvider.TWILIO.value: TwilioInputHandler,
    TelephonyProvider.EXOTEL.value: ExotelInputHandler,
    TelephonyProvider.PLIVO.value: PlivoInputHandler,
    TelephonyProvider.VOBIZ.value: VobizInputHandler,
    TelephonyProvider.SIP_TRUNK.value: SipTrunkInputHandler,
    TelephonyProvider.TALKO.value: TalkoInputHandler,
    TelephonyProvider.FREESWITCH.value: FreeSwitchInputHandler,
}
SUPPORTED_INPUT_TELEPHONY_HANDLERS: Final[dict[str, ProviderFactory]] = {
    TelephonyProvider.TWILIO.value: TwilioInputHandler,
    TelephonyProvider.EXOTEL.value: ExotelInputHandler,
    TelephonyProvider.PLIVO.value: PlivoInputHandler,
    TelephonyProvider.VOBIZ.value: VobizInputHandler,
    TelephonyProvider.SIP_TRUNK.value: SipTrunkInputHandler,
    TelephonyProvider.TALKO.value: TalkoInputHandler,
}
SUPPORTED_OUTPUT_HANDLERS: Final[dict[str, ProviderFactory]] = {
    TelephonyProvider.DEFAULT.value: DefaultOutputHandler,
    TelephonyProvider.TWILIO.value: TwilioOutputHandler,
    TelephonyProvider.EXOTEL.value: ExotelOutputHandler,
    TelephonyProvider.PLIVO.value: PlivoOutputHandler,
    TelephonyProvider.VOBIZ.value: VobizOutputHandler,
    TelephonyProvider.SIP_TRUNK.value: SipTrunkOutputHandler,
    TelephonyProvider.TALKO.value: TalkoOutputHandler,
    TelephonyProvider.FREESWITCH.value: FreeSwitchOutputHandler,
}
SUPPORTED_OUTPUT_TELEPHONY_HANDLERS: Final[dict[str, ProviderFactory]] = {
    TelephonyProvider.TWILIO.value: TwilioOutputHandler,
    TelephonyProvider.EXOTEL.value: ExotelOutputHandler,
    TelephonyProvider.PLIVO.value: PlivoOutputHandler,
    TelephonyProvider.VOBIZ.value: VobizOutputHandler,
    TelephonyProvider.SIP_TRUNK.value: SipTrunkOutputHandler,
    TelephonyProvider.TALKO.value: TalkoOutputHandler,
}
SUPPORTED_S2S_PROVIDERS: Final[dict[str, ProviderFactory]] = {
    S2SProvider.OPENAI_REALTIME.value: OpenAIRealtimeS2S,
    S2SProvider.GEMINI_LIVE.value: GeminiLiveS2S,
}
