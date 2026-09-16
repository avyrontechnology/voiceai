# legacy-shim(spec-0004) — the provider registries live in voiceai.modules.voice.registry (step B3).
"""Pure re-export of `voiceai.modules.voice.registry` (spec 0004, step B3).

The star surface is preserved verbatim for this module's star-import consumers
(task_manager.py:63, ``voiceai/models.py``) and every direct importer: the provider
classes, the five provider enums, ``elevenlabs_synthesizer``, and the nine
``SUPPORTED_*`` maps `tests/test_provider_registry_parity.py` pins. The map objects are
IDENTICAL to the registry's (never copies), so in-place monkeypatching keeps hitting
the one real registry.
"""

from voiceai.modules.voice.registry import *
