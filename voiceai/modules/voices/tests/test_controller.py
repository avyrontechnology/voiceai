"""Voices endpoints over the real factory: the prod UI call sequence (spec 0025).

Replays the failing production log (agent view → `GET /voices?agent_id=`)
against the migrated routes: create → list filtered → delete, legacy shapes,
with an owner session like the UI holds.
"""

from __future__ import annotations

from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules import auth as auth_module
from voiceai.modules import voices as voices_module
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.conftest import _JWT
from voiceai.modules.voices.models import VoiceRecord
from voiceai.modules.voices.repository import VoicesRepository
from voiceai.modules.voices.service import VoicesService
from voiceai.modules.voices.tests.test_service import _Catalog, _Definitions

BASE = "http://voices.test"
PREFIX = "/api/v1"
OWNER = {"email": "owner@x.test", "name": "Owner", "password": "owner-pass-1"}


async def _authed_client() -> AsyncClient:
    """Factory app with a default-scoped voices service; owner session attached."""
    db = InMemoryDatabase()
    inner = InMemoryRepository[VoiceRecord](db, Collections.VOICES, VoiceRecord)
    scoped = TenantScopedRepository(inner, "default", Collections.VOICES)
    service = VoicesService(
        VoicesRepository(scoped), definitions=_Definitions({"agent-1"}), catalog=_Catalog()
    )
    container = build_container(Environment())
    container.voices_service.override(providers.Object(service))
    container.auth_service.override(providers.Object(AuthService(container.auth_store(), jwt=_JWT)))
    app = create_app(env=Environment(), container=container, modules=[voices_module.MODULE, auth_module.MODULE])
    client = AsyncClient(transport=ASGITransport(app=app), base_url=BASE)
    signup = await client.post(
        f"{PREFIX}/auth/signup", json={"email": OWNER["email"], "name": OWNER["name"], "password": OWNER["password"]}
    )
    assert signup.status_code == 201, signup.text
    return client


async def test_ui_voice_sequence_serves_legacy_shapes() -> None:
    """Create → list?agent_id= → delete: 201/200/200 with the legacy keys."""
    client = await _authed_client()

    created = await client.post(
        f"{PREFIX}/voices",
        json={"agent_id": "agent-1", "name": "Nila", "provider": "elevenlabs", "provider_voice_id": "v-1"},
    )
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    assert body["agent_id"] == "agent-1"
    assert body["voice_id"].startswith("voice_")

    listed = await client.get(f"{PREFIX}/voices", params={"agent_id": "agent-1"})
    assert listed.status_code == 200, listed.text
    assert [voice["voice_id"] for voice in listed.json()["data"]["voices"]] == [body["voice_id"]]

    missing = await client.get(f"{PREFIX}/voices", params={"agent_id": "nope"})
    assert missing.status_code == 200
    assert missing.json()["data"] == {"voices": []}

    deleted = await client.delete(f"{PREFIX}/voices/{body['voice_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"state": "deleted"}

    gone = await client.get(f"{PREFIX}/voices", params={"agent_id": "agent-1"})
    assert gone.json()["data"] == {"voices": []}


async def test_foreign_agent_attach_is_a_404() -> None:
    """Attaching a voice to another tenant's agent fails without an oracle."""
    client = await _authed_client()

    response = await client.post(
        f"{PREFIX}/voices",
        json={"agent_id": "foreign", "name": "X", "provider": "elevenlabs", "provider_voice_id": "v-1"},
    )

    assert response.status_code == 404
