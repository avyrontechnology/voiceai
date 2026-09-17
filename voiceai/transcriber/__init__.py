# legacy-shim(spec-0004): this transcriber lives in voiceai.modules.voice.asr.providers (step B12c).
"""Pure re-export of `voiceai.modules.voice.asr.providers` (spec 0004, step B12c).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.asr.providers import BaseTranscriber as BaseTranscriber, DeepgramTranscriber as DeepgramTranscriber, AzureTranscriber as AzureTranscriber, SarvamTranscriber as SarvamTranscriber, AssemblyAITranscriber as AssemblyAITranscriber, GoogleTranscriber as GoogleTranscriber, PixaTranscriber as PixaTranscriber, GladiaTranscriber as GladiaTranscriber, ElevenLabsTranscriber as ElevenLabsTranscriber, SmallestTranscriber as SmallestTranscriber, OpenAITranscriber as OpenAITranscriber, SonioxTranscriber as SonioxTranscriber, GeminiTranscriber as GeminiTranscriber, TranscriberPool as TranscriberPool, LIDProvider as LIDProvider, SarvamLID as SarvamLID, SonioxLID as SonioxLID

__all__ = ["BaseTranscriber", "DeepgramTranscriber", "AzureTranscriber", "SarvamTranscriber", "AssemblyAITranscriber", "GoogleTranscriber", "PixaTranscriber", "GladiaTranscriber", "ElevenLabsTranscriber", "SmallestTranscriber", "OpenAITranscriber", "SonioxTranscriber", "GeminiTranscriber", "TranscriberPool", "LIDProvider", "SarvamLID", "SonioxLID"]
