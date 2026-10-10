"""Webhooks module units: subscription semantics, storage, and the API surface."""

from __future__ import annotations

from datetime import datetime, timezone

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.webhooks.errors import WebhookNotFoundError
from voiceai.modules.webhooks.models import Webhook
from voiceai.modules.webhooks.repository import WebhookRepository, as_webhook
from voiceai.modules.webhooks.service import WebhooksService
from voiceai.modules.webhooks.static_methods import for_agent, is_active, subscribes_to

STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _service() -> tuple[WebhooksService, WebhookRepository]:
    db = InMemoryDatabase()
    repository = WebhookRepository(InMemoryRepository(db, Collections.PLATFORM_WEBHOOKS, Webhook))
    return WebhooksService(repository), repository


# --- pure helpers -----------------------------------------------------------------------


def test_a_hook_with_no_events_subscribes_to_everything() -> None:
    """An empty subscription list means "every event", not "nothing"."""
    hook = Webhook(url="https://example.test")

    assert subscribes_to(hook, "call.completed") is True


def test_subscribes_to_filters_on_the_event_list() -> None:
    """A hook with events only receives those."""
    hook = Webhook(url="https://example.test", events=["call.completed"])

    assert subscribes_to(hook, "call.completed") is True
    assert subscribes_to(hook, "call.failed") is False


def test_for_agent_includes_tenant_wide_hooks() -> None:
    """A hook with no agent applies to every agent."""
    tenant_wide = Webhook(id="w1", url="https://a.test")
    owned = Webhook(id="w2", url="https://b.test", agent_id="agent-1")
    other = Webhook(id="w3", url="https://c.test", agent_id="agent-2")

    resolved = for_agent([tenant_wide, owned, other], "agent-1")

    assert [hook.id for hook in resolved] == ["w1", "w2"]


def test_is_active_reflects_the_flag() -> None:
    """A disabled hook reports inactive."""
    assert is_active(Webhook(url="https://a.test")) is True
    assert is_active(Webhook(url="https://a.test", enabled=False)) is False


# --- service -----------------------------------------------------------------------------


async def test_create_stores_the_destination() -> None:
    """A created hook comes back with a repository-assigned id."""
    service, _ = _service()

    created = await service.create(url="https://example.test/hook", events=["call.completed"])

    assert created.id
    assert created.enabled is True
    assert created.events == ["call.completed"]


async def test_a_tenant_wide_hook_round_trips() -> None:
    """A hook with no agent is tenant-wide and keeps its shape."""
    service, _ = _service()

    created = await service.create(url="https://example.test/hook")

    assert created.agent_id is None
    assert (await service.list_all())[0].agent_id is None


async def test_a_disabled_hook_round_trips() -> None:
    """`enabled=false` is preserved, not normalised away."""
    service, _ = _service()

    created = await service.create(url="https://example.test/hook", enabled=False)

    assert created.enabled is False


async def test_delete_tombstones_the_hook() -> None:
    """A deleted hook stops being listed."""
    service, _ = _service()
    hook = await service.create(url="https://example.test/hook")

    await service.delete(hook.id or "")

    assert await service.list_all() == []


async def test_delete_twice_raises() -> None:
    """Delete is not idempotent: the caller asked to remove something already gone."""
    service, _ = _service()
    hook = await service.create(url="https://example.test/hook")
    await service.delete(hook.id or "")

    try:
        await service.delete(hook.id or "")
    except WebhookNotFoundError:
        pass
    else:
        raise AssertionError("expected WebhookNotFoundError")


# --- repository --------------------------------------------------------------------------


async def test_list_all_walks_every_row() -> None:
    """Listing is a full walk, not a capped first page."""
    db = InMemoryDatabase()
    repository = WebhookRepository(InMemoryRepository(db, Collections.PLATFORM_WEBHOOKS, Webhook))
    for index in range(120):
        await repository.create(Webhook(url=f"https://{index}.test"))

    assert len(await repository.list_all()) == 120


def test_a_legacy_envelope_row_still_reads() -> None:
    """A row written before the migration reads as a typed webhook."""
    row = {"id": "w1", "payload": {"webhook_id": "w1", "url": "https://a.test", "events": ["call.completed"]}}

    record = as_webhook(row)

    assert record is not None
    assert record.id == "w1"
    assert record.url == "https://a.test"
    assert record.events == ["call.completed"]


def test_an_unreadable_row_is_a_miss() -> None:
    """A row we cannot read is a miss, not a crash."""
    assert as_webhook({"payload": {"nothing": 1}}) is None


async def test_a_legacy_row_is_still_listed() -> None:
    """Regression pin: a pre-migration hook must not vanish from the listing."""
    db = InMemoryDatabase()
    repository = WebhookRepository(InMemoryRepository(db, Collections.PLATFORM_WEBHOOKS, Webhook))
    db.collections.setdefault("platform_webhooks", {})["legacy-1"] = {
        "id": "legacy-1",
        "tenant_id": "tenant-a",
        "is_active": True,
        "created_at": STAMP,
        "updated_at": STAMP,
        "payload": {"webhook_id": "legacy-1", "url": "https://old.test"},
    }
    await repository.create(Webhook(url="https://new.test"))

    listed = await repository.list_all()

    assert {record.url for record in listed} == {"https://old.test", "https://new.test"}
