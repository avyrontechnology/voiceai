"""Repository-backed platform store: MemoryStore parity, tenant isolation, auth delegation (spec 0048, A)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from voiceai.common.errors import ConfigurationError, TenantNotBoundError
from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.modules.auth.repository import MongoAuthStore
from voiceai.platform.models import (
    ApiKey,
    Batch,
    Execution,
    ExecutionStatus,
    GraphDoc,
    GraphVersion,
    InboundConfig,
    Integration,
    KnowledgeBase,
    LedgerEntry,
    Organization,
    PhoneNumber,
    SessionRecord,
    SubAccount,
    Tool,
    User,
    VectorStoreConfig,
    VoiceEntry,
    Wallet,
    Webhook,
    WorkflowCampaign,
    WorkflowDoc,
    WorkflowRun,
    WorkflowVersion,
)
from voiceai.platform.repository_store import PLATFORM_COLLECTIONS, PlatformRow, RepositoryPlatformStore
from voiceai.platform.store import MemoryStore

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

Scenario = Callable[[MemoryStore], Awaitable[list[Any]]]


def _bound(tenant_id: str) -> Any:
    return bind_tenant(TenantContext(tenant_id=tenant_id, request_id="test"))


def _dump(value: Any) -> Any:
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, (Organization, Wallet)):  # defaults stamp `updated_at` at creation time
        return value.model_dump(mode="json", exclude={"updated_at"})
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


async def _run(store: MemoryStore, scenario: Scenario, tenant_id: str = TENANT_A) -> Any:
    with _bound(tenant_id):
        return _dump(await scenario(store))


# -- scenarios: the same script runs against MemoryStore and the bridge ----------------------

# Models are built once so both stores see identical default timestamps.
NUMBER_1 = PhoneNumber(number_id="n1", number="+911", created_at=T0)
NUMBER_1B = PhoneNumber(number_id="n1", number="+913", created_at=T0)
NUMBER_2 = PhoneNumber(number_id="n2", number="+912", created_at=T0)
KB_1 = KnowledgeBase(kb_id="kb1", name="docs", created_at=T0)
WEBHOOK_1 = Webhook(webhook_id="w1", url="https://example.test/hook", created_at=T0)
TOOL_A = Tool(tool_id="t1", name="a", kind="custom", agent_id="agent-1")
TOOL_B = Tool(tool_id="t2", name="b", kind="custom", agent_id="agent-2")
VOICE_A = VoiceEntry(voice_id="v1", name="a", provider="p", provider_voice_id="pv", agent_id="agent-1")
VOICE_B = VoiceEntry(voice_id="v2", name="b", provider="p", provider_voice_id="pv", agent_id="agent-2")
EXEC_OLD = Execution(
    execution_id="e1", agent_id="agent-1", to_number="+1", status=ExecutionStatus("completed"), started_at=T0
)
EXEC_NEW = Execution(
    execution_id="e2",
    agent_id="agent-1",
    batch_id="b1",
    to_number="+2",
    status=ExecutionStatus("failed"),
    started_at=T0 + timedelta(minutes=1),
)
EXEC_OTHER = Execution(
    execution_id="e3", agent_id="agent-2", to_number="+3", status=ExecutionStatus("completed"), started_at=T0
)
BATCH_1 = Batch(batch_id="b1", agent_id="agent-1", name="first", created_at=T0)
BATCH_2 = Batch(batch_id="b2", agent_id="agent-2", name="second", created_at=T0 + timedelta(minutes=1))
GRAPH_1 = GraphDoc(graph_id="g1", name="graph")
GRAPH_V2 = GraphVersion(version_id="gv2", graph_id="g1", version_number=2, name="two", created_at=T0)
GRAPH_V1 = GraphVersion(version_id="gv1", graph_id="g1", version_number=1, name="one", created_at=T0)
WORKFLOW_1 = WorkflowDoc(workflow_id="wf1", name="flow")
WF_V2 = WorkflowVersion(version_id="wv2", workflow_id="wf1", version_number=2, name="two", created_at=T0)
WF_V1 = WorkflowVersion(version_id="wv1", workflow_id="wf1", version_number=1, name="one", created_at=T0)
CAMPAIGN_1 = WorkflowCampaign(campaign_id="c1", workflow_id="wf1", name="camp")
RUN_IN = WorkflowRun(run_id="r1", workflow_id="wf1", campaign_id="c1")
RUN_OUT = WorkflowRun(run_id="r2", workflow_id="wf1")
INBOUND_1 = InboundConfig(agent_id="agent-1", greeting="hi", updated_at=T0)
VECTOR_1 = VectorStoreConfig(provider="mongodb", collection_name="c")
SUB_1 = SubAccount(sub_id="s1", name="sub")
INTEGRATION_1 = Integration(integration_id="i1", kind="calcom", name="hub")
ORG_1 = Organization(name="Otoba", updated_at=T0)
WALLET_1 = Wallet(balance_credits=42, updated_at=T0)
LEDGER_ROWS = [
    LedgerEntry(entry_id=f"l{index}", type="topup" if index % 2 else "debit", amount_credits=index, created_at=T0)
    for index in range(1, 7)
]


async def _numbers_crud(store: MemoryStore) -> list[Any]:
    await store.save_number(NUMBER_1)
    await store.save_number(NUMBER_2)
    await store.save_number(NUMBER_1B)  # same id: replace
    return [
        await store.get_number("n1"),
        await store.list_numbers(),
        await store.delete_number("n2"),
        await store.delete_number("n2"),  # already gone
        await store.get_number("n2"),
        await store.list_numbers(),
        await store.get_number("missing"),
    ]


async def _kbs_webhooks(store: MemoryStore) -> list[Any]:
    await store.save_kb(KB_1)
    await store.save_webhook(WEBHOOK_1)
    return [
        await store.get_kb("kb1"),
        await store.list_kbs(),
        await store.delete_kb("kb1"),
        await store.list_kbs(),
        await store.get_webhook("w1"),
        await store.list_webhooks(),
        await store.delete_webhook("w1"),
        await store.list_webhooks(),
    ]


async def _tools_voices_by_agent(store: MemoryStore) -> list[Any]:
    for tool in (TOOL_A, TOOL_B):
        await store.save_tool(tool)
    for voice in (VOICE_A, VOICE_B):
        await store.save_voice(voice)
    return [
        await store.list_tools(),
        await store.list_tools(agent_id="agent-2"),
        await store.get_tool("t1"),
        await store.delete_tool("t1"),
        await store.list_tools(),
        await store.list_voices(),
        await store.list_voices(agent_id="agent-1"),
        await store.delete_voice("v2"),
        await store.list_voices(),
    ]


async def _executions_filters(store: MemoryStore) -> list[Any]:
    for execution in (EXEC_OLD, EXEC_NEW, EXEC_OTHER):
        await store.save_execution(execution)
    return [
        await store.get_execution("e2"),
        await store.list_executions(),  # newest first
        await store.list_executions(agent_id="agent-1"),
        await store.list_executions(batch_id="b1"),
        await store.list_executions(status=ExecutionStatus("completed").value),
        await store.list_executions(limit=1),
        await store.list_executions(limit=1, offset=1),
        await store.list_executions(offset=5),
    ]


async def _batches(store: MemoryStore) -> list[Any]:
    await store.save_batch(BATCH_1)
    await store.save_batch(BATCH_2)
    return [await store.get_batch("b1"), await store.list_batches(), await store.list_batches(agent_id="agent-2")]


async def _graphs_and_versions(store: MemoryStore) -> list[Any]:
    await store.save_graph(GRAPH_1)
    await store.save_graph_version(GRAPH_V2)
    await store.save_graph_version(GRAPH_V1)  # sorted by version_number on read
    return [
        await store.get_graph("g1"),
        await store.list_graphs(),
        await store.list_graph_versions("g1"),
        await store.list_graph_versions("other"),
        await store.delete_graph("g1"),
        await store.list_graphs(),
    ]


async def _workflows_runs_campaigns(store: MemoryStore) -> list[Any]:
    await store.save_workflow(WORKFLOW_1)
    await store.save_workflow_version(WF_V2)
    await store.save_workflow_version(WF_V1)
    await store.save_campaign(CAMPAIGN_1)
    await store.save_workflow_run(RUN_IN)
    await store.save_workflow_run(RUN_OUT)
    return [
        await store.get_workflow("wf1"),
        await store.list_workflows(),
        await store.list_workflow_versions("wf1"),
        await store.get_campaign("c1"),
        await store.list_campaigns(),
        await store.get_workflow_run("r1"),
        await store.list_workflow_runs(),
        await store.list_workflow_runs(campaign_id="c1"),
        await store.delete_workflow("wf1"),
        await store.list_workflows(),
    ]


async def _inbound_vector_subs_integrations(store: MemoryStore) -> list[Any]:
    await store.save_inbound(INBOUND_1)
    await store.save_vector_config("agent-1", VECTOR_1)
    await store.save_sub_account(SUB_1)
    await store.save_integration(INTEGRATION_1)
    return [
        await store.get_inbound("agent-1"),
        await store.get_inbound("agent-2"),
        await store.get_vector_config("agent-1"),
        await store.get_vector_config("agent-2"),
        await store.get_sub_account("s1"),
        await store.list_sub_accounts(),
        await store.delete_sub_account("s1"),
        await store.list_sub_accounts(),
        await store.get_integration("i1"),
        await store.list_integrations(),
        await store.delete_integration("i1"),
        await store.list_integrations(),
    ]


async def _organization_wallet_ledger(store: MemoryStore) -> list[Any]:
    defaults = [await store.get_organization(), await store.get_wallet(), await store.list_ledger()]
    await store.save_organization(ORG_1)
    await store.save_wallet(WALLET_1)
    for entry in LEDGER_ROWS:
        await store.add_ledger_entry(entry)
    return [
        *defaults,
        await store.get_organization(),
        await store.get_wallet(),
        await store.list_ledger(),  # newest first
        await store.list_ledger(limit=2),
        await store.list_ledger(entry_type="topup"),
        await store.list_ledger(limit=0),
    ]


async def _reset_platform(store: MemoryStore) -> list[Any]:
    await store.save_number(NUMBER_1)
    await store.save_kb(KB_1)
    await store.save_execution(EXEC_OLD)
    await store.save_wallet(WALLET_1)
    await store.save_organization(ORG_1)
    await store.add_ledger_entry(LEDGER_ROWS[0])
    cleared = await store.reset_platform()
    return [
        cleared,
        await store.list_numbers(),
        await store.list_kbs(),
        await store.list_executions(),
        await store.get_wallet(),
        await store.list_ledger(),
        await store.get_organization(),  # organization settings survive a reset
    ]


SCENARIOS: dict[str, Scenario] = {
    "numbers_crud": _numbers_crud,
    "kbs_webhooks": _kbs_webhooks,
    "tools_voices_by_agent": _tools_voices_by_agent,
    "executions_filters": _executions_filters,
    "batches": _batches,
    "graphs_and_versions": _graphs_and_versions,
    "workflows_runs_campaigns": _workflows_runs_campaigns,
    "inbound_vector_subs_integrations": _inbound_vector_subs_integrations,
    "organization_wallet_ledger": _organization_wallet_ledger,
    "reset_platform": _reset_platform,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_bridge_matches_memory_store(bridge: RepositoryPlatformStore, name: str) -> None:
    """Every legacy entity method answers byte-identically over the repositories."""
    expected = await _run(MemoryStore(), SCENARIOS[name])
    assert await _run(bridge, SCENARIOS[name]) == expected


async def test_memory_store_still_serves_legacy_callers() -> None:
    """The async-primitive split keeps MemoryStore's own behaviour (legacy suites rely on it)."""
    store = MemoryStore()
    await store.save_number(NUMBER_1)
    assert await store.count_users() == 0
    assert [number.number_id for number in await store.list_numbers()] == ["n1"]
    assert (await store.reset_platform())["numbers"] == 1


# -- tenant isolation ---------------------------------------------------------------------


async def test_rows_are_invisible_to_other_tenants(bridge: RepositoryPlatformStore) -> None:
    with _bound(TENANT_A):
        await bridge.save_number(NUMBER_1)
        await bridge.save_organization(ORG_1)
        first_read = await bridge.get_wallet()
        assert (await bridge.get_wallet()) == first_read  # default materialised once, then stable
    with _bound(TENANT_B):
        assert await bridge.list_numbers() == []
        assert await bridge.get_number("n1") is None
        assert await bridge.delete_number("n1") is False
        assert (await bridge.get_organization()).name != ORG_1.name
        assert (await bridge.reset_platform())["numbers"] == 0
    with _bound(TENANT_A):
        assert (await bridge.get_number("n1")) is not None
        assert (await bridge.get_organization()).name == ORG_1.name


async def test_rows_carry_the_tenant_stamp(database: InMemoryDatabase, bridge: RepositoryPlatformStore) -> None:
    with _bound(TENANT_A):
        await bridge.save_number(NUMBER_1)
    documents = database.collections["platform_numbers"]
    assert [document["tenant_id"] for document in documents.values()] == [TENANT_A]
    assert documents["n1"]["payload"]["number"] == NUMBER_1.number


async def test_unbound_tenant_fails_closed(bridge: RepositoryPlatformStore) -> None:
    with pytest.raises(TenantNotBoundError):
        await bridge.save_number(NUMBER_1)
    with pytest.raises(TenantNotBoundError):
        await bridge.list_numbers()


# -- auth families delegate to the greenfield store ----------------------------------------


async def test_auth_families_have_one_home(bridge: RepositoryPlatformStore, auth_store: MongoAuthStore) -> None:
    user = User(user_id="u1", email="a@example.test", password_hash="x")
    key = ApiKey(key_id="k1", name="ci", prefix="ob_", key_hash="hash")
    with _bound(TENANT_A):
        await bridge.save_user(user)
        await bridge.save_api_key(key)
        assert await bridge.count_users() == 1
        assert (await auth_store.get_user("u1")) is not None
        assert (await bridge.get_api_key_by_hash("hash")) is not None
        assert await bridge.delete_api_key("k1") is True
        assert await bridge.delete_api_key("k1") is False
        assert await bridge.list_api_keys() == []
        assert await auth_store.list_api_keys() == []


async def test_expired_sessions_read_as_absent_and_are_retired(
    bridge: RepositoryPlatformStore, auth_store: MongoAuthStore
) -> None:
    live = SessionRecord(token_hash="live", user_id="u1", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    stale = SessionRecord(
        token_hash="stale", user_id="u1", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    with _bound(TENANT_A):
        await bridge.save_session(live)
        await bridge.save_session(stale)
        assert (await bridge.get_session("live")) is not None
        assert await bridge.get_session("stale") is None
        assert await auth_store.get_session("stale") is None


# -- composition --------------------------------------------------------------------------


def test_every_bridge_collection_is_namespaced() -> None:
    assert all(collection.value.startswith("platform_") for collection in PLATFORM_COLLECTIONS)
    assert len(set(PLATFORM_COLLECTIONS)) == len(PLATFORM_COLLECTIONS)


def test_missing_repository_is_a_configuration_error(auth_store: MongoAuthStore) -> None:
    with pytest.raises(ConfigurationError):
        RepositoryPlatformStore(auth_store, {})


def test_container_serves_one_bridge_store_offline(arch_environment: Environment) -> None:
    container = build_container(arch_environment)
    store = container.platform_store()
    assert isinstance(store, RepositoryPlatformStore)
    assert container.platform_store() is store


def test_platform_row_is_a_base_fields_document() -> None:
    row = PlatformRow(id="x", payload={"a": 1})
    assert row.is_active and row.tenant_id is None and row.payload == {"a": 1}
