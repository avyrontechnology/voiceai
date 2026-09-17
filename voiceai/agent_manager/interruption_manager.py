# legacy-shim(spec-0004): InterruptionManager lives in voiceai.modules.voice.session.interruption.
"""Pure re-export of the relocated InterruptionManager (spec 0004, B10)."""

from voiceai.modules.voice.session.interruption import InterruptionManager as InterruptionManager

__all__ = ["InterruptionManager"]
