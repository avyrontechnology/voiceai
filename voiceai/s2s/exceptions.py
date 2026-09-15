"""Module throw-surface for the s2s submodule.

Single import point for every exception type this package raises from
``voiceai.errors``. Re-exported (not subclassed) to preserve exception
identity for ``register_exception_handlers`` and existing
``pytest.raises`` contracts. This package currently imports no names
from ``voiceai.errors`` — provider failures are yielded as the local
``S2SError`` event or raised as builtin ``ConnectionError`` — so there
is nothing to re-export yet. New code that raises a shared error MUST
add an identity-preserving re-export here and import from this module,
never from ``voiceai.errors`` directly.
"""

from __future__ import annotations

__all__ = []
