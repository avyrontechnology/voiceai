# legacy-shim(spec-0004): this synthesizer lives in voiceai.modules.voice.tts.providers.kalpa_synthesizer (step B12b).
"""Pure re-export of `voiceai.modules.voice.tts.providers.kalpa_synthesizer` (spec 0004, step B12b).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.tts.providers.kalpa_synthesizer import KalpaSynthesizer as KalpaSynthesizer, KALPA_NATIVE_SAMPLE_RATE as KALPA_NATIVE_SAMPLE_RATE, MULAW_SAMPLE_RATE as MULAW_SAMPLE_RATE, KALPA_DEFAULT_MODEL as KALPA_DEFAULT_MODEL, MAX_TEXT_CHARS as MAX_TEXT_CHARS, DEFAULT_CHUNK_LENGTH_SCHEDULE as DEFAULT_CHUNK_LENGTH_SCHEDULE, RESPONSE_IDLE_TIMEOUT as RESPONSE_IDLE_TIMEOUT, AUDIO_QUALITIES as AUDIO_QUALITIES, _VOICE_IDS as _VOICE_IDS

__all__ = ["KalpaSynthesizer", "KALPA_NATIVE_SAMPLE_RATE", "MULAW_SAMPLE_RATE", "KALPA_DEFAULT_MODEL", "MAX_TEXT_CHARS", "DEFAULT_CHUNK_LENGTH_SCHEDULE", "RESPONSE_IDLE_TIMEOUT", "AUDIO_QUALITIES", "_VOICE_IDS"]
