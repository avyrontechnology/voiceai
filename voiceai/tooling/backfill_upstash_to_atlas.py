"""Operator CLI: move durable rows from the legacy Upstash Redis into Atlas (spec 0048, Slice C).

Usage:
    .venv/bin/python voiceai/tooling/backfill_upstash_to_atlas.py --census
    .venv/bin/python voiceai/tooling/backfill_upstash_to_atlas.py --apply [--prompts-dir agent_data]

``--census`` SCANs every family and prints counts — no writes anywhere. ``--apply``
copies the durable families into the greenfield stores through the container
(``DB_BACKEND``/``MONGO_URL`` from the environment; the Redis source from
``--redis-url`` or ``REDIS_URL``) and prints per-family counts. Rules:

* Atlas is truth: a row whose natural key already exists is skipped, never
  overwritten, so re-runs are safe. Run once before switching traffic and once
  right after; a row soft-deleted in Atlas between runs would be resurrected, so
  keep both runs inside the cutover window.
* Ephemera — sessions, refresh rows, throttle counters, denylist entries — are
  censused and never copied: TTL state a backfill must not resurrect.
* ``SCAN``, never ``KEYS``; payloads are never logged — keys and counts only.
* Rows that fail validation are quarantined (counted, natural key logged) and the
  run continues.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Protocol

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.common.logger import get_logger
from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.modules.agents.static_methods import is_agent_key
from voiceai.modules.auth import constants as auth_constants
from voiceai.platform.repository_store import FAMILY_COLLECTIONS

__all__ = [
    "AGENT_SCAN_PATTERN",
    "FAMILIES",
    "LEDGER_KEY",
    "LEGACY_KEY_PREFIX",
    "MIGRATED",
    "ORGANIZATION_KEY",
    "PLATFORM_FAMILIES",
    "WALLET_KEY",
    "AtlasTargets",
    "FamilyReport",
    "RedisSource",
    "apply",
    "census",
    "main",
    "_coerce_dates",
    "_model_for",
]

_LOGGER_MODULE: Final[str] = "tooling.backfill_upstash_to_atlas"
_REQUEST_ID: Final[str] = "backfill-upstash-to-atlas"
_REDIS_URL_ENV: Final[str] = "REDIS_URL"
_DEFAULT_PROMPTS_DIR: Final[str] = "agent_data"
_PROMPTS_FILE: Final[str] = "conversation_details.json"
_STRING_TYPE: Final[str] = "string"
_SCAN_COUNT: Final[int] = 500
_EVENT_LIMIT: Final[int] = 1_000_000
_ROW_FORMAT: Final[str] = "{family:<44}{seen:>8}{copied:>8}{skipped:>9}{quarantined:>13}"
_CENSUS_FORMAT: Final[str] = "{pattern:<44}{count:>8}  {description}"

#: Legacy platform key layout: ``platform:v1:<family>:<id>`` (the retired ``RedisStore``).
LEGACY_KEY_PREFIX: Final[str] = "platform:v1"
WALLET_KEY: Final[str] = f"{LEGACY_KEY_PREFIX}:wallet:singleton"
ORGANIZATION_KEY: Final[str] = f"{LEGACY_KEY_PREFIX}:org:singleton"
LEDGER_KEY: Final[str] = f"{LEGACY_KEY_PREFIX}:ledger"
#: Legacy agent definitions were bare keys (no namespace separator) holding JSON.
AGENT_SCAN_PATTERN: Final[str] = "*"

#: Legacy families the platform bridge takes verbatim (``platform_<family>`` collections).
PLATFORM_FAMILIES: Final[tuple[str, ...]] = tuple(FAMILY_COLLECTIONS)

#: (Redis glob pattern, human description). Every family the platform ever wrote.
FAMILIES: Final[tuple[tuple[str, str], ...]] = (
    (f"{LEGACY_KEY_PREFIX}:users:*", "legacy user rows (durable → auth store)"),
    (f"{LEGACY_KEY_PREFIX}:invites:*", "legacy invite rows (durable → auth store)"),
    (f"{LEGACY_KEY_PREFIX}:api_keys:*", "legacy API-key rows (durable → auth store)"),
    (f"{LEGACY_KEY_PREFIX}:auth_events:*", "legacy auth-event rows (durable → auth store)"),
    *(
        (f"{LEGACY_KEY_PREFIX}:{family}:*", f"legacy {family} rows (durable → platform_{family})")
        for family in PLATFORM_FAMILIES
    ),
    (WALLET_KEY, "legacy wallet singleton (durable → wallet module)"),
    (LEDGER_KEY, "legacy ledger list (durable → wallet module)"),
    (ORGANIZATION_KEY, "legacy organization settings (durable → platform bridge)"),
    (AGENT_SCAN_PATTERN, "legacy agent definitions: bare keys (durable → agents module)"),
    (f"{LEGACY_KEY_PREFIX}:sessions:*", "live session cache (ephemeral TTL — never migrate)"),
    (f"{LEGACY_KEY_PREFIX}:revoked:*", "revocation cache entries (ephemeral TTL — never migrate)"),
    (f"{auth_constants.THROTTLE_KEY_PREFIX}*", "login-throttle counters (ephemeral TTL — never migrate)"),
    (f"{auth_constants.DENYLIST_KEY_PREFIX}*", "revocation denylist cache (ephemeral TTL — never migrate)"),
)

#: (Redis glob pattern, Atlas collection, greenfield model name, natural key) — the auth
#: families the greenfield auth store owns. Every pattern must be censused in FAMILIES.
MIGRATED: Final[tuple[tuple[str, str, str, str], ...]] = (
    (f"{LEGACY_KEY_PREFIX}:users:*", "users", "User", "user_id"),
    (f"{LEGACY_KEY_PREFIX}:invites:*", "invites", "Invite", "invite_id"),
    (f"{LEGACY_KEY_PREFIX}:api_keys:*", "api_keys", "ApiKey", "key_id"),
    (f"{LEGACY_KEY_PREFIX}:auth_events:*", "auth_events", "AuthEvent", "event_id"),
)

#: Payload keys ending in this suffix carry ISO timestamps worth coercing for BSON.
_AT_SUFFIX: Final[str] = "_at"


class RedisSource(Protocol):
    """The slice of ``redis.asyncio.Redis`` the backfill reads through (never ``keys``)."""

    def scan_iter(self, match: str, count: int = ...) -> AsyncIterator[str]:
        """Iterate keys matching a glob without blocking the server."""
        ...

    async def get(self, name: str) -> str | None:
        """Read one string value."""
        ...

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        """Read a slice of a list (the legacy ledger)."""
        ...

    async def type(self, name: str) -> str:
        """Report a key's type, so non-string keys are never decoded as JSON."""
        ...


@dataclass
class FamilyReport:
    """Per-family outcome of one ``--apply`` run (counts only, never payloads)."""

    family: str
    seen: int = 0
    copied: int = 0
    skipped: int = 0
    quarantined: int = 0


def _model_for(model_name: str) -> Any:  # why: the table names models, callers need the class
    """Resolve a migration-table model name to its greenfield document class.

    Raises:
        KeyError: When the name is not a migrated model — a loud failure beats a
            silent family, so the census test catches table drift.
    """
    from voiceai.modules.auth.models.apikey import ApiKey
    from voiceai.modules.auth.models.audit import AuthEvent
    from voiceai.modules.auth.models.invite import Invite
    from voiceai.modules.auth.models.user import User

    return {"User": User, "Invite": Invite, "ApiKey": ApiKey, "AuthEvent": AuthEvent}[model_name]


def _coerce_dates(payload: dict[str, Any]) -> dict[str, Any]:  # why: Redis payloads are untyped JSON
    """Coerce ISO ``*_at`` strings to datetimes; leave everything else untouched.

    Unparseable values pass through unchanged — garbage must never fail a backfill,
    it is quarantined downstream, not dropped here.
    """
    coerced: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and key.endswith(_AT_SUFFIX):
            try:
                coerced[key] = datetime.fromisoformat(value)
                continue
            except ValueError:
                pass
        coerced[key] = value
    return coerced


async def _scan(redis: RedisSource, pattern: str) -> list[str]:
    """Collect the keys matching ``pattern`` with SCAN."""
    return [key async for key in redis.scan_iter(match=pattern, count=_SCAN_COUNT)]


async def _agent_keys(redis: RedisSource) -> list[str]:
    """Bare (namespace-free) string keys: the legacy agent directory."""
    keys: list[str] = []
    for key in await _scan(redis, AGENT_SCAN_PATTERN):
        if is_agent_key(key) and await redis.type(key) == _STRING_TYPE:
            keys.append(key)
    return keys


async def _json_object(redis: RedisSource, key: str) -> dict[str, Any] | None:
    """Decode one string key as a JSON object; anything else reads as ``None``."""
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


async def census(redis: RedisSource) -> list[tuple[str, int]]:
    """Count every family without writing anything.

    Returns:
        ``(pattern, count)`` per ``FAMILIES`` entry, in table order.
    """
    counts: list[tuple[str, int]] = []
    for pattern, _description in FAMILIES:
        if pattern == AGENT_SCAN_PATTERN:
            count = len(await _agent_keys(redis))
        elif pattern == LEDGER_KEY:
            count = len(await redis.lrange(LEDGER_KEY, 0, -1))
        elif pattern in (WALLET_KEY, ORGANIZATION_KEY):
            count = 1 if await redis.get(pattern) is not None else 0
        else:
            count = len(await _scan(redis, pattern))
        counts.append((pattern, count))
    return counts


class AtlasTargets:
    """Write side of the backfill: the greenfield stores behind one container, one tenant.

    Construct and use inside ``bind_tenant`` (per-request providers read the ambient
    tenant at construction).

    Args:
        container: The composed container for the target deployment.
    """

    def __init__(self, container: Any) -> None:  # why: the container is the composition root
        self._auth = container.auth_store()
        self._platform = container.platform_store()
        self._definitions = container.agent_definitions()
        self._prompts = container.agent_session_store()
        from voiceai.core.container import create_wallet_repository

        self._wallet = create_wallet_repository(container.db_client())

    async def existing_auth_ids(self, collection: str) -> set[str]:
        """Natural keys already present in Atlas for one auth family."""
        if collection == "users":
            return {row.user_id for row in await self._auth.list_users()}
        if collection == "invites":
            return {row.invite_id for row in await self._auth.list_invites()}
        if collection == "api_keys":
            return {row.key_id for row in await self._auth.list_api_keys()}
        return {row.event_id for row in await self._auth.list_auth_events(_EVENT_LIMIT)}

    async def save_auth(self, model_name: str, payload: dict[str, Any]) -> None:
        """Validate and persist one auth row through the auth store."""
        model = _model_for(model_name).model_validate(_coerce_dates(payload))
        if model_name == "User":
            await self._auth.save_user(model)
        elif model_name == "Invite":
            await self._auth.save_invite(model)
        elif model_name == "ApiKey":
            await self._auth.save_api_key(model)
        else:
            await self._auth.add_auth_event(model)

    async def restore_platform(self, family: str, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one legacy platform row into its bridge collection unless it exists."""
        written: bool = await self._platform.restore(family, item_id, payload)
        return written

    async def restore_organization(self, payload: dict[str, Any]) -> bool:
        """Seed the organization settings singleton unless one exists."""
        written: bool = await self._platform.restore_organization(payload)
        return written

    async def ledger_is_empty(self) -> bool:
        """Report whether the module ledger has no rows yet (wallet + ledger copy once)."""
        return not await self._wallet.list_ledger(limit=1)

    async def restore_wallet(self, payload: dict[str, Any]) -> None:
        """Persist the legacy wallet balance as the module singleton."""
        from voiceai.modules.wallet.constants import SINGLETON_WALLET_ID
        from voiceai.modules.wallet.models import Wallet

        await self._wallet.save_wallet(
            Wallet(id=SINGLETON_WALLET_ID, balance_credits=payload["balance_credits"], currency=payload["currency"])
        )

    async def restore_ledger_entry(self, payload: dict[str, Any]) -> None:
        """Persist one legacy ledger entry under its legacy id."""
        from voiceai.modules.wallet.models import LedgerEntry

        fields: dict[str, Any] = {
            "id": payload["entry_id"],
            "type": payload["type"],
            "amount_credits": payload["amount_credits"],
            "reason": payload.get("reason"),
        }
        created_at = _coerce_dates(payload).get("created_at")
        if isinstance(created_at, datetime):
            fields["created_at"] = created_at
        await self._wallet.add_ledger_entry(LedgerEntry(**fields))

    async def restore_agent(self, agent_id: str, config: dict[str, Any]) -> bool:
        """Persist one legacy agent definition unless the id already exists."""
        if await self._definitions.get_agent(agent_id) is not None:
            return False
        await self._definitions.save_agent(agent_id, config)
        return True

    async def restore_prompts(self, agent_id: str, prompts: dict[str, Any]) -> bool:
        """Persist one legacy prompt file unless prompts already exist for the agent."""
        if await self._prompts.get_prompts(agent_id) is not None:
            return False
        await self._prompts.save_prompts(agent_id, prompts)
        return True


async def _apply_auth(redis: RedisSource, targets: AtlasTargets, reports: list[FamilyReport]) -> None:
    logger = get_logger(_LOGGER_MODULE)
    for pattern, collection, model_name, natural_key in MIGRATED:
        report = FamilyReport(family=collection)
        existing = await targets.existing_auth_ids(collection)
        for key in await _scan(redis, pattern):
            report.seen += 1
            payload = await _json_object(redis, key)
            if payload is None or not payload.get(natural_key):
                report.quarantined += 1
                logger.warning("quarantined %s row %s", collection, key)
                continue
            if payload[natural_key] in existing:
                report.skipped += 1
                continue
            try:
                await targets.save_auth(model_name, payload)
            except Exception as exc:  # noqa: BLE001 - one bad row must never stop the run
                report.quarantined += 1
                logger.warning("quarantined %s row %s (%s)", collection, key, type(exc).__name__)
                continue
            report.copied += 1
        reports.append(report)


async def _apply_platform(redis: RedisSource, targets: AtlasTargets, reports: list[FamilyReport]) -> None:
    logger = get_logger(_LOGGER_MODULE)
    for family in PLATFORM_FAMILIES:
        report = FamilyReport(family=f"platform_{family}")
        prefix = f"{LEGACY_KEY_PREFIX}:{family}:"
        for key in await _scan(redis, f"{prefix}*"):
            report.seen += 1
            payload = await _json_object(redis, key)
            item_id = key[len(prefix) :]
            if payload is None or not item_id:
                report.quarantined += 1
                logger.warning("quarantined %s row %s", family, key)
                continue
            if await targets.restore_platform(family, item_id, payload):
                report.copied += 1
            else:
                report.skipped += 1
        reports.append(report)


async def _apply_singletons(redis: RedisSource, targets: AtlasTargets, reports: list[FamilyReport]) -> None:
    organization = FamilyReport(family="organization")
    payload = await _json_object(redis, ORGANIZATION_KEY)
    if payload is not None:
        organization.seen = 1
        if await targets.restore_organization(payload):
            organization.copied = 1
        else:
            organization.skipped = 1
    reports.append(organization)

    wallet = FamilyReport(family="wallet+ledger")
    wallet_payload = await _json_object(redis, WALLET_KEY)
    ledger_raw = await redis.lrange(LEDGER_KEY, 0, -1)
    wallet.seen = (1 if wallet_payload is not None else 0) + len(ledger_raw)
    if wallet.seen == 0:
        reports.append(wallet)
        return
    if not await targets.ledger_is_empty():
        wallet.skipped = wallet.seen
        reports.append(wallet)
        return
    if wallet_payload is not None:
        await targets.restore_wallet(wallet_payload)
        wallet.copied += 1
    for raw in reversed(ledger_raw):  # LPUSH order: newest first on the wire, oldest first in Atlas
        try:
            entry = json.loads(raw)
            if not isinstance(entry, dict):
                raise TypeError("ledger entry is not an object")
            await targets.restore_ledger_entry(entry)
        except Exception as exc:  # noqa: BLE001 - one bad row must never stop the run
            wallet.quarantined += 1
            get_logger(_LOGGER_MODULE).warning("quarantined ledger entry (%s)", type(exc).__name__)
            continue
        wallet.copied += 1
    reports.append(wallet)


async def _apply_agents(redis: RedisSource, targets: AtlasTargets, reports: list[FamilyReport]) -> None:
    logger = get_logger(_LOGGER_MODULE)
    report = FamilyReport(family="agents")
    for key in await _agent_keys(redis):
        report.seen += 1
        config = await _json_object(redis, key)
        if config is None:
            report.quarantined += 1
            logger.warning("quarantined agent %s", key)
            continue
        try:
            written = await targets.restore_agent(key, config)
        except Exception as exc:  # noqa: BLE001 - one bad row must never stop the run
            report.quarantined += 1
            logger.warning("quarantined agent %s (%s)", key, type(exc).__name__)
            continue
        report.copied += int(written)
        report.skipped += int(not written)
    reports.append(report)


async def _apply_prompts(prompts_dir: Path, targets: AtlasTargets, reports: list[FamilyReport]) -> None:
    logger = get_logger(_LOGGER_MODULE)
    report = FamilyReport(family="agent_prompts")
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob(f"*/{_PROMPTS_FILE}")):
            report.seen += 1
            agent_id = path.parent.name
            try:
                prompts = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(prompts, dict):
                    raise TypeError("prompt file is not an object")
                written = await targets.restore_prompts(agent_id, prompts)
            except Exception as exc:  # noqa: BLE001 - one bad file must never stop the run
                report.quarantined += 1
                logger.warning("quarantined prompts for %s (%s)", agent_id, type(exc).__name__)
                continue
            report.copied += int(written)
            report.skipped += int(not written)
    reports.append(report)


async def apply(
    redis: RedisSource, container: Any, *, tenant_id: str = DEFAULT_TENANT_ID, prompts_dir: Path | None = None
) -> list[FamilyReport]:
    """Copy every durable family into Atlas under ``tenant_id``; ephemera are never touched.

    Args:
        redis: The legacy Redis source.
        container: The composed container for the target deployment.
        tenant_id: The tenant every migrated row is stamped with (single-tenant history).
        prompts_dir: The legacy ``agent_data`` directory, or ``None`` to skip prompts.

    Returns:
        One report per family, in run order.
    """
    reports: list[FamilyReport] = []
    with bind_tenant(TenantContext(tenant_id=tenant_id, request_id=_REQUEST_ID)):
        targets = AtlasTargets(container)
        await _apply_auth(redis, targets, reports)
        await _apply_platform(redis, targets, reports)
        await _apply_singletons(redis, targets, reports)
        await _apply_agents(redis, targets, reports)
        await _apply_prompts(prompts_dir or Path(_DEFAULT_PROMPTS_DIR), targets, reports)
    return reports


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the backfill."""
    parser = argparse.ArgumentParser(description="Move durable legacy Redis rows into Atlas (spec 0048).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--census", action="store_true", help="Count every family; write nothing.")
    group.add_argument("--apply", action="store_true", help="Copy durable families into Atlas.")
    parser.add_argument("--redis-url", default=None, help=f"Source Redis URL (default: ${_REDIS_URL_ENV}).")
    parser.add_argument("--prompts-dir", default=_DEFAULT_PROMPTS_DIR, help="Legacy agent_data directory.")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_ID, help="Tenant every migrated row is stamped with.")
    return parser.parse_args(argv)


def _default_redis(url: str) -> Any:  # why: the driver type stays out of the tooling surface
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(url, decode_responses=True)


async def _run(
    args: argparse.Namespace, redis_factory: Callable[[str], Any], container_factory: Callable[[], Any]
) -> int:
    url = args.redis_url or os.environ.get(_REDIS_URL_ENV, "")
    if not url:
        print(f"no source: pass --redis-url or set {_REDIS_URL_ENV}", file=sys.stderr)
        return 2
    redis = redis_factory(url)
    try:
        if args.census:
            print(_CENSUS_FORMAT.format(pattern="pattern", count="count", description="family"))
            descriptions = dict(FAMILIES)
            for pattern, count in await census(redis):
                print(_CENSUS_FORMAT.format(pattern=pattern, count=count, description=descriptions[pattern]))
            return 0
        container = container_factory()
        try:
            reports = await apply(redis, container, tenant_id=args.tenant, prompts_dir=Path(args.prompts_dir))
        finally:
            from voiceai.core.container import aclose_container

            await aclose_container(container)
        print(
            _ROW_FORMAT.format(
                family="family", seen="seen", copied="copied", skipped="skipped", quarantined="quarantined"
            )
        )
        for report in reports:
            print(_ROW_FORMAT.format(**report.__dict__))
        return 0
    finally:
        closer = getattr(redis, "aclose", None)
        if closer is not None:
            await closer()


def _default_container() -> Any:
    from voiceai.core.container import build_container
    from voiceai.core.environment import load_environment

    return build_container(load_environment())


def main(
    argv: Sequence[str] | None = None,
    *,
    redis_factory: Callable[[str], Any] = _default_redis,
    container_factory: Callable[[], Any] = _default_container,
) -> int:
    """CLI entry point; returns the process exit code."""
    return asyncio.run(_run(_parse_args(argv), redis_factory, container_factory))


if __name__ == "__main__":  # pragma: no cover - operator entry
    sys.exit(main())
