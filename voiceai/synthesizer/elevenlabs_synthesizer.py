# legacy-shim(spec-0004): this synthesizer lives in voiceai.modules.voice.tts.providers.elevenlabs_synthesizer (step B12b).
"""Pure re-export of `voiceai.modules.voice.tts.providers.elevenlabs_synthesizer` (spec 0004, step B12b).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.tts.providers.elevenlabs_synthesizer import ElevenlabsBase as ElevenlabsBase, ElevenlabsSynthesizer as ElevenlabsSynthesizer, ElevenlabsV3Synthesizer as ElevenlabsV3Synthesizer, STABILITY_PRESETS as STABILITY_PRESETS, DEFAULT_STABILITY as DEFAULT_STABILITY, CLOSE_TIMEOUT_S as CLOSE_TIMEOUT_S, RECONNECT_POLL_INTERVAL_S as RECONNECT_POLL_INTERVAL_S, KEEP_ALIVE_INTERVAL_S as KEEP_ALIVE_INTERVAL_S

__all__ = ["ElevenlabsBase", "ElevenlabsSynthesizer", "ElevenlabsV3Synthesizer", "STABILITY_PRESETS", "DEFAULT_STABILITY", "CLOSE_TIMEOUT_S", "RECONNECT_POLL_INTERVAL_S", "KEEP_ALIVE_INTERVAL_S"]
