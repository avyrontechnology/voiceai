"""TTS package: synthesizer bases, pool, stream loop and providers (spec 0004, B12b).

Relocated verbatim from ``voiceai/synthesizer/`` (base/pool/stream) and its provider
modules (``tts/providers/``, including the kalpa split); every old path is a
``# legacy-shim(spec-0004)`` identity re-export. Providers bind their legacy lookups
through ``adapters.tts_runtime`` (R3).
"""

from __future__ import annotations
