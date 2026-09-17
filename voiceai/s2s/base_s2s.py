# legacy-shim(spec-0004) — the base class lives in voiceai.modules.voice.s2s.base (step B5).
"""Pure re-export keeping ``voiceai.s2s.base_s2s`` imports (the port-conformance pin in
`tests/arch/modules/voice/test_ports.py`) resolving by identity; deleted at cutover."""

from voiceai.modules.voice.s2s.base import MAX_RECONNECT_ATTEMPTS, RECONNECT_DELAY_S, BaseS2SProvider

__all__ = ["MAX_RECONNECT_ATTEMPTS", "RECONNECT_DELAY_S", "BaseS2SProvider"]
