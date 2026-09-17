"""The synthesizer provider package surface (spec 0004, B12b)."""

from voiceai.modules.voice.tts.base import BaseSynthesizer
from voiceai.modules.voice.tts.pool import SynthesizerPool
from voiceai.modules.voice.tts.stream import StreamSynthesizer

from .azure_synthesizer import AzureSynthesizer
from .cartesia_synthesizer import CartesiaSynthesizer
from .deepgram_synthesizer import DeepgramSynthesizer
from .elevenlabs_synthesizer import ElevenlabsSynthesizer, ElevenlabsV3Synthesizer
from .kalpa_synthesizer import KalpaSynthesizer
from .maya_synthesizer import MayaSynthesizer
from .openai_synthesizer import OPENAISynthesizer
from .pixa_synthesizer import PixaSynthesizer
from .polly_synthesizer import PollySynthesizer
from .rime_synthesizer import RimeSynthesizer
from .sarvam_synthesizer import SarvamSynthesizer
from .smallest_synthesizer import SmallestSynthesizer

__all__ = [
    "BaseSynthesizer",
    "StreamSynthesizer",
    "PollySynthesizer",
    "ElevenlabsSynthesizer",
    "ElevenlabsV3Synthesizer",
    "OPENAISynthesizer",
    "DeepgramSynthesizer",
    "AzureSynthesizer",
    "CartesiaSynthesizer",
    "RimeSynthesizer",
    "SmallestSynthesizer",
    "SarvamSynthesizer",
    "PixaSynthesizer",
    "MayaSynthesizer",
    "KalpaSynthesizer",
    "SynthesizerPool",
]
