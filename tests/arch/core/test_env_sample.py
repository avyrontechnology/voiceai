"""`.env.sample` stays complete and loadable (spec 0048, deploy-config review pass).

The sample is the operator's template: every `Environment` field must be named in it, no
value may carry an inline comment (python-dotenv and Docker Compose `env_file` disagree on
those), and loading it must build the single-app configuration. Nothing here reads the
developer's own `.env`: the process environment is scrubbed of every field name around each
test and restored afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

from voiceai.core.environment import DB_BACKEND_MONGO, Environment, load_environment, reset_environment

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_SAMPLE = REPO_ROOT / ".env.sample"
INLINE_COMMENT_MARKER = "#"
ENVIRONMENT_NAMES: tuple[str, ...] = tuple(name.upper() for name in Environment.model_fields)


@pytest.fixture(autouse=True)
def scrubbed_process_environment() -> Iterator[None]:
    """Remove every `Environment` variable for the test and put the originals back afterwards.

    `load_environment` writes the file's values into `os.environ`; a plain `monkeypatch.delenv`
    cannot undo a write to a name that was absent before the test, so this restores by hand.
    """
    saved = {name: os.environ.get(name) for name in ENVIRONMENT_NAMES}
    for name in ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    reset_environment()
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    reset_environment()


def test_every_environment_field_is_named_in_the_sample() -> None:
    """A field added to `Environment` without a sample entry drifts the operator template."""
    names = set(dotenv_values(ENV_SAMPLE))
    missing = sorted(set(ENVIRONMENT_NAMES) - names)
    assert missing == []


def test_no_value_carries_an_inline_comment() -> None:
    """Comments live on their own line so python-dotenv and Compose read the same value."""
    leaked = {
        name: value
        for name, value in dotenv_values(ENV_SAMPLE).items()
        if value is not None and INLINE_COMMENT_MARKER in value
    }
    assert leaked == {}


def test_the_sample_loads_as_the_single_app_configuration() -> None:
    """The template validates as-is and selects the spec 0048 process shape."""
    env = load_environment(ENV_SAMPLE)
    assert env.db_backend == DB_BACKEND_MONGO
    assert env.voice_ws_enabled is True
