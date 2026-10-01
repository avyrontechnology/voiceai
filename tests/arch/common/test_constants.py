"""Constants that are computed rather than written down: the application version."""

from __future__ import annotations

import importlib.metadata

from voiceai.common.constants import _FALLBACK_VERSION, APP_VERSION


class TestAppVersion:
    """`/health` and the FastAPI app must report the deployed package version."""

    def test_app_version_is_the_installed_distribution_version(self) -> None:
        # An installed checkout (`pip install -e .`) reports the distribution version; a raw
        # checkout has no metadata and must report the fallback sentinel instead — a real pin
        # in both environments, never a skip and never a deselect (spec 0051).
        try:
            expected = importlib.metadata.version("voiceai")
        except importlib.metadata.PackageNotFoundError:
            expected = _FALLBACK_VERSION
        assert APP_VERSION == expected

    def test_app_version_is_a_non_empty_string(self) -> None:
        # The fallback (`0.0.0+local`) also satisfies this; the constant is never blank.
        assert isinstance(APP_VERSION, str)
        assert APP_VERSION
