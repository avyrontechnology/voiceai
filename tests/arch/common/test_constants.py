"""Constants that are computed rather than written down: the application version."""

from __future__ import annotations

import importlib.metadata

from voiceai.common.constants import APP_VERSION


class TestAppVersion:
    """`/health` and the FastAPI app must report the deployed package version."""

    def test_app_version_is_the_installed_distribution_version(self) -> None:
        assert APP_VERSION == importlib.metadata.version("voiceai")

    def test_app_version_is_a_non_empty_string(self) -> None:
        # The fallback (`0.0.0+local`) also satisfies this; the constant is never blank.
        assert isinstance(APP_VERSION, str)
        assert APP_VERSION
