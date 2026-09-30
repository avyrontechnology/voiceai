"""Legacy platform surface: frozen routers served by the single app (spec 0048).

`single_app_routers()` is what `create_app` mounts; `RepositoryPlatformStore` is
what production persists through; `MemoryStore` remains for tests.
"""

from voiceai.platform.repository_store import RepositoryPlatformStore
from voiceai.platform.router import single_app_routers
from voiceai.platform.store import MemoryStore

__all__ = ["MemoryStore", "RepositoryPlatformStore", "single_app_routers"]
