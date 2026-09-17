"""IO package: telephony and default input/output handlers (spec 0004, B12a).

Relocated verbatim from ``voiceai/input_handlers/`` and
``voiceai/output_handlers/`` (including talko); every old path is a
``# legacy-shim(spec-0004)`` identity re-export. Handler modules bind their
legacy lookups (packet builder, duration probe, constants) through
``adapters.io_runtime`` (R3).
"""

from __future__ import annotations
