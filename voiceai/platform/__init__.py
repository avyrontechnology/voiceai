"""VoiceAI platform layer (SaaS surface over the realtime engine).

Provides executions, batches, phone numbers, knowledge bases, tools,
webhooks, wallet and agent templates. Storage is pluggable: in-memory
for tests/dev, Redis in production (same backend as agent CRUD).
"""

from voiceai.platform.router import create_platform_app
from voiceai.platform.store import MemoryStore, RedisStore

__all__ = ["MemoryStore", "RedisStore", "create_platform_app"]
