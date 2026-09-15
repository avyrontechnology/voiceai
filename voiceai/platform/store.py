"""Back-compat shim: canonical home is ``voiceai.platform.repositories``.

Emits ``DeprecationWarning`` with the removal version (research R-03).
No logic here — see ``repositories/memory.py`` and ``repositories/redis.py``.
"""

from __future__ import annotations

import importlib
import warnings

__all__ = ["MemoryStore", "RedisStore"]

_REMOVAL = "v0.12.0"


def __getattr__(name: str) -> object:
    """Lazily resolve ``MemoryStore``/``RedisStore`` from the canonical package."""
    if name in __all__:
        warnings.warn(
            f"voiceai.platform.store.{name} moved to voiceai.platform.repositories; the shim is removed in {_REMOVAL}",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(importlib.import_module("voiceai.platform.repositories"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the shimmed names for introspection."""
    return sorted(__all__)
