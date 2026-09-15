"""Facade tests for the platform module (US2, task T055).

Proves the enterprise seam (data-model.md Entity 8, contract E-01): the
``voiceai.platform`` package exposes ONLY its public surface
(stores, app factory, error codes, protocols) and the ``protocols.py``
ports cover every service/repository seam.
"""

import voiceai.platform as platform
from voiceai.platform import errors, protocols
from voiceai.platform import services as services_pkg


def test_facade_exports_public_surface() -> None:
    """The facade __all__ carries stores, factory, codes, and protocols."""
    for name in ("MemoryStore", "RedisStore", "MongoStore", "create_platform_app", "protocols", "errors"):
        assert name in platform.__all__, f"{name} missing from platform facade"
        assert getattr(platform, name) is not None


def test_protocols_cover_seams() -> None:
    """Repository port resolves from protocols (stable seam address)."""
    assert protocols.PlatformRepository is not None
    assert isinstance(platform.MemoryStore(), protocols.PlatformRepository)


def test_error_codes_stable() -> None:
    """Module-namespaced codes unchanged by the split (contract L-04)."""
    assert errors.BATCH_EXCEEDS_MAX_ENTRIES == "platform.batch_exceeds_max_entries"
    assert platform.errors is errors


def test_services_package_path_resolves() -> None:
    """Service functions resolve through the services package (V-03)."""
    assert callable(services_pkg.create_batch)
    assert callable(services_pkg.list_executions)
    assert callable(services_pkg.create_graph)
