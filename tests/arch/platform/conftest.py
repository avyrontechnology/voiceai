"""Shared fixtures for the platform bridge suites (spec 0048)."""

from __future__ import annotations

import pytest

from voiceai.core.container import create_auth_store, create_platform_store
from voiceai.core.db import InMemoryDatabase
from voiceai.modules.auth.repository import MongoAuthStore
from voiceai.platform.repository_store import RepositoryPlatformStore


@pytest.fixture
def database() -> InMemoryDatabase:
    return InMemoryDatabase()


@pytest.fixture
def auth_store(database: InMemoryDatabase) -> MongoAuthStore:
    store: MongoAuthStore = create_auth_store(database, None)
    return store


@pytest.fixture
def bridge(database: InMemoryDatabase, auth_store: MongoAuthStore) -> RepositoryPlatformStore:
    store: RepositoryPlatformStore = create_platform_store(database, auth_store)
    return store
