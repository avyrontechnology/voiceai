"""Platform persistence package (contract L-03).

Canonical home for the neutral ``PlatformRepository`` Protocol (``base``)
and the three backends (``memory``/``redis``/``mongo``). Old module paths
(``store``/``mongo_store``/``repositories``) resolve here via lazy shims.
"""

from voiceai.platform.repositories.base import PlatformRepository
from voiceai.platform.repositories.memory import MemoryStore
from voiceai.platform.repositories.mongo import COLLECTION_KEY_BY_MODEL, MongoStore
from voiceai.platform.repositories.redis import RedisStore

__all__ = [
    "COLLECTION_KEY_BY_MODEL",
    "MemoryStore",
    "MongoStore",
    "PlatformRepository",
    "RedisStore",
]
