"""The one true tenant-scoped key builder (spec 0019, M0)."""

import pytest

from voiceai.common.keys import tenant_key


def test_tenant_key_prefixes_everything_with_the_tenant() -> None:
    """Format is t:{tenant}:{namespace}[:parts], always tenant-first."""
    assert tenant_key("acme", "call", "c-1") == "t:acme:call:c-1"
    assert tenant_key("acme", "throttle") == "t:acme:throttle"


def test_tenant_key_refuses_unscoped_builds() -> None:
    """Empty tenant or namespace raises instead of building a leaky key."""
    with pytest.raises(ValueError):
        tenant_key("", "call", "c-1")
    with pytest.raises(ValueError):
        tenant_key("acme", "")
