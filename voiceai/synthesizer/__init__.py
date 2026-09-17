# legacy-shim(spec-0004): this synthesizer lives in voiceai.modules.voice.tts.providers (step B12b).
"""Pure re-export of `voiceai.modules.voice.tts.providers` (spec 0004, step B12b).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.tts.providers import BaseSynthesizer as BaseSynthesizer, StreamSynthesizer as StreamSynthesizer, PollySynthesizer as PollySynthesizer, ElevenlabsSynthesizer as ElevenlabsSynthesizer, ElevenlabsV3Synthesizer as ElevenlabsV3Synthesizer, OPENAISynthesizer as OPENAISynthesizer, DeepgramSynthesizer as DeepgramSynthesizer, AzureSynthesizer as AzureSynthesizer, CartesiaSynthesizer as CartesiaSynthesizer, RimeSynthesizer as RimeSynthesizer, SmallestSynthesizer as SmallestSynthesizer, SarvamSynthesizer as SarvamSynthesizer, PixaSynthesizer as PixaSynthesizer, MayaSynthesizer as MayaSynthesizer, KalpaSynthesizer as KalpaSynthesizer, SynthesizerPool as SynthesizerPool

__all__ = ["BaseSynthesizer", "StreamSynthesizer", "PollySynthesizer", "ElevenlabsSynthesizer", "ElevenlabsV3Synthesizer", "OPENAISynthesizer", "DeepgramSynthesizer", "AzureSynthesizer", "CartesiaSynthesizer", "RimeSynthesizer", "SmallestSynthesizer", "SarvamSynthesizer", "PixaSynthesizer", "MayaSynthesizer", "KalpaSynthesizer", "SynthesizerPool"]
