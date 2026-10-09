
"""Container ports: structural protocols for dependency injection (spec 0067).

Services depend on these abstractions, never on concrete implementations.
The container wires concrete types; tests inject fakes.
"""

from __future__ import annotations

from voiceai.modules.auth.ports import AuthStorePort, TokenService
from voiceai.modules.auth.repos.mongo_user_repo import MongoUserRepo

#: Session store port — re-exported from auth module.
SessionStore = AuthStorePort

#: Token service port — JWT mint/verify seam.
UserRepo = MongoUserRepo

__all__ = [
    "SessionStore",
    "TokenService",
    "UserRepo",
]
