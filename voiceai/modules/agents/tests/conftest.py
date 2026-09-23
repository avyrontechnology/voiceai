"""Agents-test hygiene: per-test resets (T1 colocated-test template, copied for T3).

Shared doubles (`arch_environment`, `client_factory`, `arch_app`, ...) resolve from
the repo-root `conftest.py` by fixture name — this file owns only the autouse hooks
every module tree needs, so a test here can never leak env or request-id state.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from voiceai.common.logger import set_request_id
from voiceai.core.environment import reset_environment


@pytest.fixture(autouse=True)
def reset_environment_cache() -> Iterator[None]:
    """Drop the cached `Environment` around every test so env vars cannot leak between them."""
    reset_environment()
    yield
    reset_environment()


@pytest.fixture(autouse=True)
def reset_request_id() -> Iterator[None]:
    """Clear the correlation contextvar after each test: it lives in the thread's context."""
    yield
    set_request_id(None)
