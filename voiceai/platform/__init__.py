"""VoiceAI platform layer (SaaS surface over the realtime engine).

Provides executions, batches, phone numbers, knowledge bases, tools,
webhooks, wallet and agent templates. Storage is pluggable: in-memory
for tests/dev, Redis in production (same backend as agent CRUD).

Public surface only (contract E-01): stores, app factory, error codes,
and seam protocols. Service functions live at
``voiceai.platform.services``; persistence lives at
``voiceai.platform.repositories``.
"""

from voiceai.platform import errors, protocols
from voiceai.platform.controllers import create_platform_app
from voiceai.platform.repositories import MemoryStore, MongoStore, RedisStore

__all__ = [
    "MemoryStore",
    "MongoStore",
    "RedisStore",
    "create_platform_app",
    "errors",
    "protocols",
]
