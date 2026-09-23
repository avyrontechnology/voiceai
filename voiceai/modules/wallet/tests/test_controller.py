"""Wallet endpoints end to end: real factory, real module, ASGI transport (T5).

The wallet gates still resolve through the legacy `platform.auth` chain (their
migration into `modules/auth` is a later turn), so each test seeds a platform
`MemoryStore` admin session behind `app.state.platform_store` — the exact seam
production quickstart serves. The wallet rows themselves ride the greenfield
`MongoWalletRepository` over in-memory collections.
"""

from __future__ import annotations

from dependency_injector import providers

from voiceai.common.constants import API_PREFIX
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.auth.constants import SESSION_COOKIE
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.repository import MongoAuthStore
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.conftest import _JWT
from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
from voiceai.modules.wallet.repository import MongoWalletRepository
from voiceai.modules.wallet.service import WalletService

WALLET_URL = f"{API_PREFIX}/wallet"
TEMPLATES_URL = f"{API_PREFIX}/templates"


async def _admin_client(arch_app, client_factory):
    """Factory app with greenfield wallet rows and an owner session cookie.

    Gates resolve through the container `AuthService` (no legacy seam): the owner
    signs up through the service, and the legacy session cookie authenticates.
    """
    database = InMemoryDatabase()
    auth_store = MongoAuthStore(
        users=InMemoryRepository(database, Collections.USERS, User),
        sessions=InMemoryRepository(database, Collections.SESSIONS, SessionRecord),
        invites=InMemoryRepository(database, Collections.INVITES, Invite),
        keys=InMemoryRepository(database, Collections.API_KEYS, ApiKey),
        events=InMemoryRepository(database, Collections.AUTH_EVENTS, AuthEvent),
        revoked=InMemoryRepository(database, Collections.REVOKED_TOKENS, RevokedToken),
    )
    arch_app.state.container.auth_service.override(providers.Object(AuthService(auth_store, jwt=_JWT)))
    templates = InMemoryRepository(database, Collections.AGENT_TEMPLATES, StoredTemplate)
    seed = StoredTemplate(
        template_id="tmpl-t1",
        name="T1",
        industry="x",
        description="seed row",
        languages=["en"],
        agent_payload={"hello": "world"},
    )
    seed.id = seed.template_id
    await templates.insert(seed)
    service = WalletService(
        MongoWalletRepository(
            InMemoryRepository(database, Collections.WALLETS, Wallet),
            InMemoryRepository(database, Collections.LEDGER, LedgerEntry),
            templates,
        )
    )
    arch_app.state.container.wallet_service.override(providers.Object(service))
    _, tokens = await arch_app.state.container.auth_service().signup("owner@x.test", "Owner", "owner-pass-1")
    client = client_factory(arch_app)
    client.cookies.set(SESSION_COOKIE, tokens.legacy_token)
    return client


async def test_wallet_round_trip_topup_and_ledger(arch_app, client_factory) -> None:
    """Balance starts at zero, top-up lands, ledger shows the entry."""
    async with await _admin_client(arch_app, client_factory) as client:
        empty = await client.get(WALLET_URL)
        assert empty.status_code == 200, empty.text
        assert empty.json()["data"]["balance_credits"] == 0.0

        topped = await client.post(WALLET_URL + "/topup", json={"amount_credits": 12.5, "reason": "t5"})
        assert topped.status_code == 200, topped.text
        assert topped.json()["data"]["balance_credits"] == 12.5

        ledger = await client.get(WALLET_URL + "/ledger")
        assert ledger.status_code == 200
        rows = ledger.json()["data"]["entries"]
        assert len(rows) == 1 and rows[0]["amount_credits"] == 12.5


async def test_ledger_limit_is_bounded(arch_app, client_factory) -> None:
    """An over-large page reads 422 (bounded pagination, rule 2)."""
    async with await _admin_client(arch_app, client_factory) as client:
        response = await client.get(WALLET_URL + "/ledger", params={"limit": 100000})

    assert response.status_code == 422


async def test_templates_list_get_and_404(arch_app, client_factory) -> None:
    """Catalog lists the seeded row without payloads; full view carries it; unknown 404s."""
    async with await _admin_client(arch_app, client_factory) as client:
        listed = await client.get(TEMPLATES_URL)
        assert listed.status_code == 200, listed.text
        summaries = listed.json()["data"]["templates"]
        assert [s["template_id"] for s in summaries] == ["tmpl-t1"]
        assert "agent_payload" not in summaries[0]

        full = await client.get(f"{TEMPLATES_URL}/tmpl-t1")
        assert full.status_code == 200
        assert full.json()["data"]["agent_payload"] == {"hello": "world"}

        missing = await client.get(f"{TEMPLATES_URL}/no-such-template")
        assert missing.status_code == 404
        assert missing.json()["ok"] is False


async def test_anonymous_wallet_reads_401(arch_app, client_factory) -> None:
    """No credential → 401 error envelope (no legacy seam involved)."""
    async with client_factory(arch_app) as client:
        response = await client.get(WALLET_URL)

    assert response.status_code == 401
    assert response.json()["ok"] is False
