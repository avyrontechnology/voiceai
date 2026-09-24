"""Ambient tenant context: vocabulary without producers yet (spec 0020, M1a)."""

import pytest

from voiceai.common import (
    SYSTEM_TENANT_ID,
    TenantContext,
    TenantNotBoundError,
    bind_tenant,
    current_tenant,
    reset_tenant,
)


@pytest.fixture(autouse=True)
def _isolated_binding() -> None:
    """Each case starts unbound; ContextVar state never leaks between tests."""
    reset_tenant()


def test_unset_context_raises_opaque_500() -> None:
    """Fail-loud, never a default tenant: unset reads are wiring bugs."""
    with pytest.raises(TenantNotBoundError) as exc_info:
        current_tenant()
    assert exc_info.value.http_status == 500
    assert "ref " in exc_info.value.public_message


def test_bind_makes_context_ambient() -> None:
    """Inside the scope the exact context object is returned."""
    context = TenantContext(tenant_id="acme", request_id="r-1")
    with bind_tenant(context):
        assert current_tenant() is context


def test_bind_restores_outer_binding_on_exit() -> None:
    """Nesting composes: inner exit restores the outer tenant, not unbound."""
    outer = TenantContext(tenant_id="acme", request_id="r-1")
    inner = TenantContext(tenant_id="globex", request_id="r-2")
    with bind_tenant(outer):
        with bind_tenant(inner):
            assert current_tenant() is inner
        assert current_tenant() is outer
    with pytest.raises(TenantNotBoundError):
        current_tenant()


def test_reset_clears_the_binding() -> None:
    """Teardown helper returns reads to the fail-loud state."""
    with bind_tenant(TenantContext(tenant_id="acme", request_id="r-1")):
        reset_tenant()
        with pytest.raises(TenantNotBoundError):
            current_tenant()


def test_context_is_frozen_and_fully_shaped() -> None:
    """Blueprint shape now so M1b adds producers, not fields."""
    context = TenantContext(
        tenant_id="acme",
        request_id="r-1",
        workspace_id="w-1",
        principal_id="u-1",
        scopes=frozenset({"calls:read"}),
        plan="pro",
    )
    assert context.plan == "pro"
    assert "calls:read" in context.scopes
    with pytest.raises(AttributeError):
        context.tenant_id = "globex"  # type: ignore[misc]


def test_defaults_carry_nothing_privileged() -> None:
    """Minimal construction grants no workspace, principal, or scopes."""
    context = TenantContext(tenant_id="acme", request_id="r-1")
    assert context.workspace_id is None
    assert context.principal_id is None
    assert context.scopes == frozenset()


def test_system_tenant_constant() -> None:
    """Platform-global rows have one spelled-out owner, not a magic string."""
    assert SYSTEM_TENANT_ID == "system"
