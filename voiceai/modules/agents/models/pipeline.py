"""Media-pipeline schema: transcriber, synthesizer + TTS provider configs, IO, and S2S.

Moved verbatim from ``voiceai/models.py`` lines 45-247 and 612-679 (spec 0002, step A2);
only import statements changed, plus one structural conversion: ``Synthesizer.preprocess``
dispatches through the ``SYNTHESIZER_PROVIDER_CONFIGS`` class map (mirroring the existing
``S2S_PROVIDER_CONFIGS`` pattern) instead of the legacy if/elif chain, so this package
imports zero engine code (canary-asserted by the arch tests).
"""

from __future__ import annotations

from typing import Any, Final, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from voiceai.enums import (
    ReasoningEffort,
    S2SProvider,
    SynthesizerProvider,
    TelephonyProvider,
    TranscriberProvider,
)
from voiceai.modules.agents.constants import MODEL_REASONING_EFFORT_MAP
from voiceai.modules.agents.models.agent import validate_attribute, validate_reasoning_effort_for_model


class PollyConfig(BaseModel):
    """Amazon Polly voice settings."""

    voice: str
    engine: str
    language: str
    # volume: Optional[str] = '0dB'
    # rate: Optional[str] = '100%'


class ElevenLabsConfig(BaseModel):
    """ElevenLabs voice settings."""

    voice: str
    voice_id: str
    model: str
    temperature: float | None = 0.5
    similarity_boost: float | None = 0.75
    speed: float | None = 1.0
    style: float | None = 0.0


class OpenAIConfig(BaseModel):
    """OpenAI TTS voice settings."""

    voice: str
    model: str


class DeepgramConfig(BaseModel):
    """Deepgram Aura voice settings."""

    voice_id: str
    voice: str
    model: str


class CartesiaConfig(BaseModel):
    """Cartesia voice settings."""

    voice_id: str
    voice: str
    model: str
    language: str
    speed: float | None = 1.0


class RimeConfig(BaseModel):
    """Rime voice settings."""

    voice_id: str
    language: str
    voice: str
    model: str


class SmallestConfig(BaseModel):
    """Smallest.ai voice settings."""

    voice_id: str
    language: str
    voice: str
    model: str


class SarvamConfig(BaseModel):
    """Sarvam voice settings."""

    voice_id: str
    language: str
    voice: str
    model: str
    speed: float | None = 1.0


class PixaConfig(BaseModel):
    """Pixa voice settings."""

    voice_id: str
    voice: str
    model: str
    language: str
    top_p: float | None = 0.95
    repetition_penalty: float | None = 1.3


class MayaConfig(BaseModel):
    """Maya voice settings."""

    # "Ananya" (female) or "Arjun" (male) — the only two voices, both speak every language.
    # Case-sensitive: Maya rejects "ananya" with a 400.
    voice_id: str
    voice: str
    model: str
    # One of hi/bn/gu/kn/ml/mr/or/pa/ta/te/en/auto. "en" is Indian English, "auto" lets Maya
    # detect per utterance. Region-qualified codes ("en-IN") reduce to the primary subtag.
    language: str | None = "en"


class KalpaConfig(BaseModel):
    """Kalpa voice settings."""

    voice: str = "Kiara"
    voice_id: str | None = None
    model: str = "kalpa-tts-multilingual-beta-v0.1"
    temperature: float | None = None
    acoustic_temperature: float | None = None
    max_new_tokens: int | None = None
    audio_quality: str | None = None
    chunk_length_schedule: list[int] | None = None


class AzureConfig(BaseModel):
    """Azure TTS voice settings."""

    voice: str
    model: str
    language: str
    speed: float | None = 1.0


class Transcriber(BaseModel):
    """Speech-to-text pipeline configuration."""

    model: str | None = Field(default="nova-2", description="The transcriber model to use.")
    language: str | None = Field(default=None, description="Language code for transcription (e.g., 'en', 'es').")
    stream: bool = Field(default=False, description="Whether to stream audio data to the transcriber.")
    sampling_rate: int | None = Field(default=16000, description="Audio sampling rate in Hz.")
    encoding: str | None = Field(default="linear16", description="Audio encoding format.")
    endpointing: int | None = Field(
        default=500, description="Duration of silence in ms to trigger endpointing (utterance completion)."
    )
    keywords: str | None = Field(default=None, description="Comma-separated keywords to boost transcription accuracy.")
    task: str | None = Field(default="transcribe", description="Task type, usually 'transcribe'.")
    provider: str | None = Field(default="deepgram", description="The speech-to-text provider to use.")
    multilingual: dict[str, Any] | None = Field(default=None, description="Multilingual configuration settings.")
    active: str | None = Field(default=None, description="Active status identifier.")
    # Flux model parameters
    eot_threshold: float | None = Field(default=None, description="End-of-turn threshold for flux models.")
    eager_eot_threshold: float | None = Field(default=None, description="Eager end-of-turn threshold.")
    eot_timeout_ms: int | None = Field(default=None, description="End-of-turn timeout in milliseconds.")
    language_hints: list[str] | None = Field(
        default=None, description="List of probable languages to hint the transcriber."
    )
    delay: str | None = Field(default="medium", description="Delay configuration ('low', 'medium', 'high').")
    noise_reduction: bool | None = Field(
        default=False, description="Whether to apply noise reduction to the incoming audio."
    )
    vad_threshold: float | None = Field(default=0.5, description="Voice Activity Detection (VAD) confidence threshold.")
    vad_prefix_padding_ms: int | None = Field(
        default=300, description="Padding in milliseconds applied before VAD triggers."
    )

    @field_validator("provider")
    def validate_model(cls, value: str | None) -> str | None:
        """Reject an unknown transcriber provider."""
        return validate_attribute(value, TranscriberProvider.all_values())


#: Provider name -> pydantic config class for the ``Synthesizer.preprocess`` dispatch. The
#: legacy if/elif chain became this map (spec 0002, step A2) so the models package needs no
#: engine imports; keys are the enum values, mirroring ``S2S_PROVIDER_CONFIGS`` below.
SYNTHESIZER_PROVIDER_CONFIGS: Final[dict[str, type[BaseModel]]] = {
    SynthesizerProvider.ELEVENLABS.value: ElevenLabsConfig,
    SynthesizerProvider.PIXA.value: PixaConfig,
    SynthesizerProvider.CARTESIA.value: CartesiaConfig,
    SynthesizerProvider.POLLY.value: PollyConfig,
    SynthesizerProvider.AZURETTS.value: AzureConfig,
    SynthesizerProvider.DEEPGRAM.value: DeepgramConfig,
    SynthesizerProvider.OPENAI.value: OpenAIConfig,
    SynthesizerProvider.SMALLEST.value: SmallestConfig,
    SynthesizerProvider.SARVAM.value: SarvamConfig,
    SynthesizerProvider.RIME.value: RimeConfig,
    SynthesizerProvider.MAYA.value: MayaConfig,
    SynthesizerProvider.KALPA.value: KalpaConfig,
}


class Synthesizer(BaseModel):
    """Text-to-speech pipeline configuration with provider-specific config dispatch."""

    provider: str = Field(
        ..., description="The text-to-speech provider to use (e.g., 'elevenlabs', 'polly', 'deepgram')."
    )
    provider_config: (
        PollyConfig
        | ElevenLabsConfig
        | AzureConfig
        | RimeConfig
        | SmallestConfig
        | SarvamConfig
        | PixaConfig
        | CartesiaConfig
        | DeepgramConfig
        | OpenAIConfig
        | MayaConfig
        | KalpaConfig
    ) = Field(..., description="Provider-specific configuration details.", union_mode="smart")
    stream: bool = Field(default=False, description="Whether to stream synthesized audio back to the client.")
    buffer_size: int | None = Field(
        default=40, description="Buffer size in characters before sending text to the synthesizer."
    )
    audio_format: str | None = Field(default="pcm", description="Audio format for the synthesized output.")
    caching: bool | None = Field(default=True, description="Enable caching of frequently synthesized phrases.")

    @model_validator(mode="before")
    def preprocess(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Coerce a dict ``provider_config`` into its provider's config class.

        Behavior-identical to the legacy per-provider if/elif chain: unknown providers
        pass through untouched (the field validator rejects them later), non-dict configs
        are left to the smart union, and the ElevenLabs guard runs first, exactly as
        before.
        """
        provider = values.get("provider")
        config = values.get("provider_config", {})

        if provider == SynthesizerProvider.ELEVENLABS.value and (not config.get("voice") or not config.get("voice_id")):
            raise ValueError("ElevenLabs config requires 'voice' or 'voice_id'.")

        # The legacy chain compared with ==, which never hashed a non-string provider.
        config_cls = SYNTHESIZER_PROVIDER_CONFIGS.get(provider) if isinstance(provider, str) else None
        if config_cls is not None and isinstance(config, dict):
            values["provider_config"] = config_cls(**config)

        return values

    @field_validator("provider")
    def validate_model(cls, value: str) -> str:
        """Reject an unknown synthesizer provider."""
        return validate_attribute(value, SynthesizerProvider.all_values())


class IOModel(BaseModel):
    """Input/output stream configuration for one telephony/IO provider."""

    provider: str
    format: str | None = "wav"

    @field_validator("provider")
    def validate_provider(cls, value: str) -> str:
        """Reject an unknown IO provider."""
        return validate_attribute(value, TelephonyProvider.all_values())


class OpenAIRealtimeConfig(BaseModel):
    """OpenAI Realtime (speech-to-speech) session configuration."""

    model: str = Field(default="gpt-realtime-2.1", description="The OpenAI Realtime model ID to use.")
    voice: str = Field(default="marin", description="The default voice to use for audio generation.")
    # Playback rate (0.25 to 1.5), not how the reply is worded.
    speed: float | None = Field(default=1.0, description="Playback rate of the generated audio (0.25 to 1.5).")
    # semantic_vad scores whether the caller has actually finished from what they said, so
    # it waits longer on a trailing "ummm" than on a finished sentence. That is the job the
    # llm pipeline does with a word count and a phrase list, done by a model instead.
    turn_detection_type: str = Field(
        default="semantic_vad", description="Type of turn detection: 'semantic_vad' or 'server_vad'."
    )
    # auto | low | medium | high. Lower gives the caller longer before the model takes over.
    eagerness: str | None = Field(
        default="auto", description="How eagerly the model responds (auto, low, medium, high)."
    )
    # server_vad only; ignored under semantic_vad.
    vad_threshold: float | None = Field(default=0.5, description="VAD threshold (for server_vad only).")
    vad_silence_duration_ms: int | None = Field(
        default=500, description="Silence duration in ms before triggering VAD."
    )
    vad_prefix_padding_ms: int | None = Field(default=300, description="Prefix padding for VAD in ms.")
    reasoning_effort: ReasoningEffort | None = Field(
        default=None, description="Reasoning effort level (if supported by model)."
    )
    max_output_tokens: int | None = Field(default=None, description="Maximum output tokens for generation.")
    transcription_model: str | None = Field(
        default="gpt-4o-mini-transcribe", description="Model used for transcribing input audio."
    )
    language: str | None = Field(default=None, description="Language constraint for the session.")

    @model_validator(mode="after")
    def validate_reasoning(self) -> OpenAIRealtimeConfig:
        """Reject a reasoning effort the realtime model does not support."""
        if self.reasoning_effort:
            if self.model not in MODEL_REASONING_EFFORT_MAP:
                raise ValueError(f"reasoning_effort is not supported for realtime model '{self.model}'.")
            validate_reasoning_effort_for_model(self.model, self.reasoning_effort.value)
        return self


class GeminiLiveConfig(BaseModel):
    """Gemini Live (speech-to-speech) session configuration."""

    model: str = Field(default="gemini-3.1-flash-live-preview", description="The Gemini Live model ID to use.")
    voice: str = Field(default="Kore", description="The default voice to use.")
    language: str | None = Field(default=None, description="Language setting.")
    temperature: float | None = Field(default=None, description="Temperature for generation.")
    start_sensitivity: str | None = Field(default=None, description="Voice activation start sensitivity.")
    end_sensitivity: str | None = Field(default=None, description="Voice activation end sensitivity.")
    # Gemini's guide puts the usable band at 500-800ms: below it utterances fragment and
    # transcription quality drops, above it the caller waits on every reply.
    vad_silence_duration_ms: int | None = Field(default=600, description="VAD silence duration in ms.")
    vad_prefix_padding_ms: int | None = Field(default=None, description="Prefix padding for VAD.")
    # Gemini closes an audio session at ~15 minutes, so both stay on unless explicitly disabled.
    enable_session_resumption: bool = Field(
        default=True, description="Whether to resume the session gracefully if it closes automatically."
    )
    enable_context_compression: bool = Field(
        default=True, description="Whether to compress context to save tokens over long sessions."
    )


S2S_PROVIDER_CONFIGS: Final[dict[str, type[BaseModel]]] = {
    S2SProvider.OPENAI_REALTIME.value: OpenAIRealtimeConfig,
    S2SProvider.GEMINI_LIVE.value: GeminiLiveConfig,
}


class S2SConfig(BaseModel):
    """Speech-to-speech provider selection plus its provider-specific configuration."""

    provider: str = Field(..., description="The S2S multimodal provider, e.g. 'openai_realtime' or 'gemini_live'.")
    provider_config: OpenAIRealtimeConfig | GeminiLiveConfig = Field(
        ..., description="Configuration specific to the chosen S2S provider."
    )
    # Suppresses inbound audio while the agent opens, so its own greeting cannot trip provider VAD.
    welcome_audio_gate_ms: int = Field(
        default=1500,
        description=(
            "Milliseconds to suppress inbound audio at connection start to avoid VAD tripping on the agent's greeting."
        ),
    )

    @model_validator(mode="before")
    def preprocess(cls, values: Any) -> Any:  # why: mode="before" receives arbitrary client input
        """Validate the provider name and coerce its config through the class map."""
        if not isinstance(values, dict):
            return values
        provider = values.get("provider")
        validate_attribute(provider, S2SProvider.all_values())
        config = values.get("provider_config") or {}
        if isinstance(config, BaseModel):
            config = config.model_dump()
        # cast: validate_attribute above raised unless provider is a known map key.
        values["provider_config"] = S2S_PROVIDER_CONFIGS[cast(str, provider)](**config)
        return values
