"""Upstash → Atlas backfill: census counts, idempotent copies, ephemera untouched (spec 0048, C)."""

from __future__ import annotations

import fnmatch
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import VoiceAIContainer, build_container, create_wallet_repository
from voiceai.core.environment import Environment
from voiceai.platform.models import User as PlatformUser
from voiceai.tooling.backfill_upstash_to_atlas import (
    AGENT_SCAN_PATTERN,
    FAMILIES,
    LEDGER_KEY,
    LEGACY_KEY_PREFIX,
    MIGRATED,
    ORGANIZATION_KEY,
    WALLET_KEY,
    FamilyReport,
    apply,
    census,
    main,
)


class FakeSource:
    """SCAN/GET/LRANGE/TYPE over a dict: strings hold JSON text, lists hold JSON rows."""

    def __init__(self, strings: dict[str, str], lists: dict[str, list[str]] | None = None) -> None:
        self._strings = dict(strings)
        self._lists = dict(lists or {})
        self.keys_calls = 0

    async def scan_iter(self, match: str, count: int = 10) -> AsyncIterator[str]:
        for key in sorted({*self._strings, *self._lists}):
            if fnmatch.fnmatchcase(key, match):
                yield key

    async def get(self, name: str) -> str | None:
        return self._strings.get(name)

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        rows = self._lists.get(name, [])
        return rows if end == -1 else rows[start : end + 1]

    async def type(self, name: str) -> str:
        return "string" if name in self._strings else "list" if name in self._lists else "none"

    async def keys(self, pattern: str) -> list[str]:  # the forbidden call: the tool must never use it
        self.keys_calls += 1
        raise AssertionError("KEYS is banned")


def _key(family: str, item_id: str) -> str:
    return f"{LEGACY_KEY_PREFIX}:{family}:{item_id}"


def _seeded_source() -> FakeSource:
    strings = {
        _key("users", "u1"): json.dumps(
            {"user_id": "u1", "email": "u1@x.test", "password_hash": "legacy", "role": "owner"}
        ),
        _key("users", "u2"): json.dumps(
            {"user_id": "u2", "email": "u2@x.test", "password_hash": "h2", "role": "member"}
        ),
        _key("users", "bad"): "not json",
        _key("invites", "i1"): json.dumps(
            {"invite_id": "i1", "email": "new@x.test", "token_hash": "t", "expires_at": "2030-01-01T00:00:00+00:00"}
        ),
        _key("api_keys", "k1"): json.dumps({"key_id": "k1", "name": "ci", "prefix": "sk_", "key_hash": "kh"}),
        _key("auth_events", "e1"): json.dumps({"event_id": "e1", "type": "login"}),
        _key("numbers", "n1"): json.dumps({"number_id": "n1", "number": "+15550000001", "assigned_agent_id": "a1"}),
        _key("numbers", "n2"): json.dumps({"number_id": "n2", "number": "+15550000002"}),
        _key("webhooks", "w1"): json.dumps({"webhook_id": "w1", "url": "https://example.test/hook"}),
        _key("executions", "e1"): json.dumps({"execution_id": "e1", "agent_id": "a1", "to_number": "+1"}),
        _key("kbs", "kb1"): json.dumps({"kb_id": "kb1", "name": "docs"}),
        WALLET_KEY: json.dumps({"balance_credits": 42, "currency": "credits"}),
        ORGANIZATION_KEY: json.dumps({"org_id": "default", "name": "Legacy Org"}),
        "a1": json.dumps({"agent_name": "Existing", "channels": ["voice"]}),
        "a2": json.dumps({"agent_name": "Fresh", "channels": ["voice"]}),
        "broken-agent": "{{not json",
        "foo:bar": json.dumps({"ignored": True}),
        _key("sessions", "s1"): json.dumps(
            {"token_hash": "s1", "user_id": "u1", "expires_at": "2030-01-01T00:00:00+00:00"}
        ),
        _key("revoked", "j1"): json.dumps({"jti": "j1"}),
        "auth:throttle:1.2.3.4": "3",
        "auth:denied:j1": "1",
    }
    lists = {
        LEDGER_KEY: [  # LPUSH order: newest first
            json.dumps(
                {"entry_id": "l3", "type": "debit", "amount_credits": 3, "created_at": "2026-01-03T00:00:00+00:00"}
            ),
            json.dumps(
                {"entry_id": "l2", "type": "topup", "amount_credits": 2, "created_at": "2026-01-02T00:00:00+00:00"}
            ),
            json.dumps(
                {"entry_id": "l1", "type": "topup", "amount_credits": 1, "created_at": "2026-01-01T00:00:00+00:00"}
            ),
        ]
    }
    return FakeSource(strings, lists)


@pytest.fixture
def container(arch_environment: Environment) -> VoiceAIContainer:
    return build_container(arch_environment)


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    for agent_id in ("a1", "a2"):
        folder = tmp_path / agent_id
        folder.mkdir()
        (folder / "conversation_details.json").write_text(json.dumps({"task_1": {"system_prompt": agent_id}}))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "conversation_details.json").write_text("[]")
    return tmp_path


def _bound() -> Any:
    return bind_tenant(TenantContext(tenant_id=DEFAULT_TENANT_ID, request_id="test"))


def _report(reports: list[FamilyReport], family: str) -> FamilyReport:
    (report,) = [row for row in reports if row.family == family]
    return report


async def _seed_atlas(container: VoiceAIContainer) -> None:
    """Rows that already live in Atlas: the backfill must never overwrite them."""
    await container.auth_store().save_user(
        PlatformUser(user_id="u1", email="u1@x.test", password_hash="atlas-truth", role="owner")
    )
    with _bound():
        await container.agent_definitions().save_agent("a1", {"agent_name": "Atlas", "channels": ["voice"]})


# -- census -----------------------------------------------------------------------------


async def test_census_counts_every_family_without_keys() -> None:
    source = _seeded_source()
    counts = dict(await census(source))
    assert counts[f"{LEGACY_KEY_PREFIX}:users:*"] == 3
    assert counts[f"{LEGACY_KEY_PREFIX}:numbers:*"] == 2
    assert counts[AGENT_SCAN_PATTERN] == 3  # a1, a2, broken-agent (bare string keys); foo:bar is namespaced
    assert counts[LEDGER_KEY] == 3
    assert counts[WALLET_KEY] == 1 and counts[ORGANIZATION_KEY] == 1
    assert counts[f"{LEGACY_KEY_PREFIX}:sessions:*"] == 1
    assert counts["auth:throttle:*"] == 1 and counts["auth:denied:*"] == 1
    assert source.keys_calls == 0
    assert set(counts) == {pattern for pattern, _ in FAMILIES}


def test_every_migrated_family_is_censused_and_ephemera_never_migrate() -> None:
    censused = {pattern for pattern, _ in FAMILIES}
    assert {row[0] for row in MIGRATED} <= censused
    for word in ("sessions", "revoked", "throttle", "denied"):
        assert not any(word in row[0] for row in MIGRATED), word


# -- apply ------------------------------------------------------------------------------


async def test_apply_copies_durable_rows_once_and_never_overwrites(
    container: VoiceAIContainer, prompts_dir: Path
) -> None:
    await _seed_atlas(container)
    source = _seeded_source()

    reports = await apply(source, container, prompts_dir=prompts_dir)

    users = _report(reports, "users")
    assert (users.seen, users.copied, users.skipped, users.quarantined) == (3, 1, 1, 1)
    atlas_user = await container.auth_store().get_user("u1")
    assert atlas_user is not None and atlas_user.password_hash == "atlas-truth"  # Atlas is truth
    assert (await container.auth_store().get_user("u2")) is not None
    assert [row.key_id for row in await container.auth_store().list_api_keys()] == ["k1"]
    assert (await container.auth_store().get_session("s1")) is None  # ephemera never copied

    numbers = _report(reports, "platform_numbers")
    assert (numbers.seen, numbers.copied, numbers.skipped) == (2, 2, 0)
    with _bound():
        store = container.platform_store()
        assert sorted(row.number_id for row in await store.list_numbers()) == ["n1", "n2"]
        assert (await store.get_webhook("w1")) is not None
        assert (await store.get_execution("e1")) is not None
        assert (await store.get_organization()).name == "Legacy Org"
        wallet_repo = create_wallet_repository(container.db_client())
        assert (await wallet_repo.get_wallet()).balance_credits == 42
        assert [row.id for row in await wallet_repo.list_ledger(limit=10)][::-1] == ["l1", "l2", "l3"]
        assert (await container.agent_definitions().get_agent("a1"))["agent_name"] == "Atlas"  # kept
        assert (await container.agent_definitions().get_agent("a2"))["agent_name"] == "Fresh"
        assert (await container.agent_session_store().get_prompts("a2")) == {"task_1": {"system_prompt": "a2"}}
    agents = _report(reports, "agents")
    assert (agents.seen, agents.copied, agents.skipped, agents.quarantined) == (3, 1, 1, 1)
    prompts = _report(reports, "agent_prompts")
    assert (prompts.seen, prompts.copied, prompts.quarantined) == (3, 2, 1)
    assert source.keys_calls == 0

    again = await apply(source, container, prompts_dir=prompts_dir)
    assert sum(row.copied for row in again) == 0
    assert _report(again, "wallet+ledger").skipped == 4


async def test_apply_stamps_the_requested_tenant(container: VoiceAIContainer, tmp_path: Path) -> None:
    await apply(_seeded_source(), container, tenant_id="acme", prompts_dir=tmp_path)
    with bind_tenant(TenantContext(tenant_id="acme", request_id="t")):
        assert len(await container.platform_store().list_numbers()) == 2
    with _bound():
        assert await container.platform_store().list_numbers() == []


# -- CLI --------------------------------------------------------------------------------


def test_cli_census_and_apply(container: VoiceAIContainer, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _seeded_source()
    factory = lambda url: source  # noqa: E731 - test double factory
    assert main(["--census", "--redis-url", "redis://ignored"], redis_factory=factory) == 0
    out = capsys.readouterr().out
    assert f"{LEGACY_KEY_PREFIX}:numbers:*" in out and "redis://ignored" not in out
    code = main(
        ["--apply", "--redis-url", "redis://ignored", "--prompts-dir", str(tmp_path)],
        redis_factory=factory,
        container_factory=lambda: container,
    )
    assert code == 0
    assert "platform_numbers" in capsys.readouterr().out


def test_cli_refuses_to_run_without_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert main(["--census"], redis_factory=lambda url: FakeSource({})) == 2
