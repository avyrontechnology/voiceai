"""Wire-contract pins: bodies validate through the DTOs, garbage does not (T2).

Controllers return `JSONResponse` envelopes (so `response_model` is OpenAPI truth,
not runtime serialisation) — these tests are the runtime enforcement: live login
bodies must validate through `AuthContract.LoginResponse`, and malformed payloads
must fail DTO validation rather than reaching the service.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.common.constants import API_PREFIX
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.schemas import AuthContract
from voiceai.modules.auth.tests.test_controller import _client
from voiceai.platform.store import MemoryStore

PREFIX = f"{API_PREFIX}/auth"


async def test_login_body_matches_the_dto() -> None:
    """The password flow answers identity plus the token pair."""
    async with await _client(MemoryStore()) as client:
        await client.post(f"{PREFIX}/signup", json={"email": "o@x.test", "name": "O", "password": "owner-pass-1"})
        response = await client.post(f"{PREFIX}/login", json={"email": "o@x.test", "password": "owner-pass-1"})

    assert response.status_code == 200
    parsed = AuthContract.LoginResponse.model_validate(response.json()["data"])
    assert parsed.token_type == C.BEARER_SCHEME
    assert parsed.access_token.count(".") == 2
    assert parsed.expires_in == 900
    assert parsed.user.email == "o@x.test"


def test_dto_rejects_short_passwords_and_bad_roles() -> None:
    """Field constraints hold at the boundary, before any service runs."""
    with pytest.raises(ValidationError):
        AuthContract.SignupRequest.model_validate({"email": "a@x.test", "password": "short"})
    with pytest.raises(ValidationError):
        AuthContract.SetRoleRequest.model_validate({"role": "superuser"})
    with pytest.raises(ValidationError):
        AuthContract.LoginResponse.model_validate(
            {"user": {}, "scopes": [], "access_token": "x", "token_type": "bearer", "expires_in": 0}
        )
