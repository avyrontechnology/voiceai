"""Back-compat shim: canonical home is ``voiceai.platform.repositories``.

Emits ``DeprecationWarning`` with the removal version (research R-03).
No logic here — see ``repositories/mongo.py``.
"""

from __future__ import annotations

import importlib
import warnings

__all__ = ["COLLECTION_KEY_BY_MODEL", "MongoStore"]

_REMOVAL = "v0.12.0"


def __getattr__(name: str) -> object:
    """Lazily resolve ``MongoStore``/``COLLECTION_KEY_BY_MODEL`` from the canonical package."""
    if name in __all__:
        warnings.warn(
            f"voiceai.platform.mongo_store.{name} moved to voiceai.platform.repositories; "
            f"the shim is removed in {_REMOVAL}",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(importlib.import_module("voiceai.platform.repositories"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the shimmed names for introspection."""
    return sorted(__all__)
