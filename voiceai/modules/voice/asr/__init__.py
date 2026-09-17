"""ASR package: transcriber base, pool and providers (spec 0004, B12c).

Relocated verbatim from ``voiceai/transcriber/`` (base/pool plus all providers,
including the deepgram 4-way split); every old path is a
``# legacy-shim(spec-0004)`` identity re-export. Providers bind their legacy lookups
through ``adapters.asr_runtime`` (R3).
"""

from __future__ import annotations
