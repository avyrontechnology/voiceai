"""Full OAuth2 login connector: authorize URL, callback, refresh (spec 0061).

Completes what DCR started: DCR registers the *client*, this service logs
the *user* in — provider login page (`POST /oauth/authorize-url`) →
browser callback (`GET /oauth/callback`, PKCE) → sealed tokens → on-demand
refresh (`POST /partner-credentials/{id}/refresh`).

Trust boundaries: `tenant_id` always comes from the server-side state
record (never query/body); `state` is single-use + TTL-bound (CSRF/replay
safe); PKCE S256 always; secrets seal immediately (the ≤10-minute Redis
state is the only place a client secret rests outside the vault, same
posture as session cookies in Redis — documented in the spec).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import secrets
from collections.abc import Callable
from typing import Any, Final, Protocol
from urllib.parse import urlencode

import httpx

from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError
from voiceai.modules.tools.vault import CredentialVault
from voiceai.modules.tools.vault_models import PartnerCredential
from voiceai.modules.tools.vault_repository import VaultRepository

__all__ = ["OAuthService", "RedisStatePort"]

logger: logging.Logger = get_logger(C.MODULE_NAME)

#: Redis key pinning one login handshake (single-use, TTL-bound).
_STATE_KEY_PREFIX: Final[str] = "oauth:state:"
#: Login handshake lifetime, seconds (spec 0061: 10 minutes).
_STATE_TTL_S: Final[int] = 600
#: PKCE code verifier entropy, bytes (43+ chars after base64url).
_VERIFIER_BYTES: Final[int] = 64
#: State token entropy, bytes (64 hex chars).
_STATE_BYTES: Final[int] = 32
#: OAuth callback path under the API prefix (pinned — providers register it).
_CALLBACK_PATH: Final[str] = "/api/v1/oauth/callback"
#: Token HTTP timeout, seconds (mandatory on every outbound call).
_TOKEN_TIMEOUT_S: Final[float] = 10.0
#: POSTed-message type the callback page emits (no secrets, ever).
_POSTMESSAGE_TYPE: Final[str] = "oauth:connected"


class RedisStatePort(Protocol):
    """TTL state seam for login handshakes (structural, None means dark)."""

    async def get(self, key: str) -> str | None:
        """Return the state JSON, or `None` on miss/expiry.

        Args:
            key: The namespaced state key.

        Returns:
            The stored JSON, or `None`.
        """
        ...

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Store state JSON with a TTL.

        Args:
            key: The namespaced state key.
            ttl: Seconds the entry stays valid.
            value: The state JSON.
        """
        ...

    async def delete(self, key: str) -> None:
        """Drop one state entry (single-use consumption).

        Args:
            key: The namespaced state key.
        """
        ...


def _b64url(raw: bytes) -> str:
    """Encode without padding (OAuth PKCE/base64url spelling).

    Args:
        raw: Bytes to encode.

    Returns:
        Unpadded base64url text.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    """Mint a PKCE verifier + S256 challenge (always S256, never plain).

    Returns:
        `(verifier, challenge)`.
    """
    verifier = _b64url(secrets.token_bytes(_VERIFIER_BYTES))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _callback_url(public_base_url: str) -> str:
    """Pin the redirect URI for this deployment (spec 0061: exactly one).

    Args:
        public_base_url: Operator-configured public origin.

    Returns:
        The absolute callback URL providers redirect back to.
    """
    return f"{public_base_url.rstrip('/')}{_CALLBACK_PATH}"


def _page(ok: bool, provider: str, detail: str, status_code: int = 200) -> tuple[str, int]:
    """Render the callback popup page (provider + status only, never secrets).

    Args:
        ok: Whether the login completed.
        provider: Partner name (HTML-escaped — it rides caller input).
        detail: Short safe reason shown to the user.
        status_code: HTTP status for the page.

    Returns:
        `(html, status_code)`.
    """
    safe_provider = html.escape(provider or "provider")
    safe_detail = html.escape(detail)
    # `<` never survives into the script block (even inside the JSON string —
    # `</script>` there would break out): unicode-escaped per the JSON spec.
    payload = json.dumps({"type": _POSTMESSAGE_TYPE, "ok": ok, "provider": provider}).replace("<", "\\u003c")
    page = (
        "<!doctype html><html><body>"
        f"<p>{safe_provider}: {safe_detail}</p>"
        "<script>"
        f"try{{if(window.opener){{window.opener.postMessage({payload},'*');}}}}catch(e){{}}"
        "window.close();"
        "</script></body></html>"
    )
    return page, status_code


class OAuthService:
    """OAuth2 login over tenant vault views + TTL state (spec 0061).

    Args:
        repos_for: Vault views factory keyed by tenant (callback storage
            follows the state record's tenant, never ambient guesses).
        vault: Envelope encryption for tokens (fails closed when dark).
        redis: Handshake state (`None` keeps login dark with a loud 400).
        public_base_url: Deployment origin pinning the callback URL (empty
            keeps login dark — paste/DCR still work).
        http_factory: Token-endpoint client seam (tests inject MockTransport).
    """

    def __init__(
        self,
        repos_for: Callable[[str], VaultRepository],
        vault: CredentialVault,
        redis: RedisStatePort | None,
        public_base_url: str,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._repos_for = repos_for
        self._vault = vault
        self._redis = redis
        self._public_base_url = public_base_url
        self._http_factory = http_factory

    def _require_redis(self, what: str) -> RedisStatePort:
        """Return the state backend or fail closed (login needs Redis).

        Args:
            what: Operation name (identifiers only, for the 400 details).

        Returns:
            The state backend.

        Raises:
            InvalidToolError: When Redis is not wired (HTTP 400).
        """
        if self._redis is None:
            raise InvalidToolError(
                "OAuth login needs Redis for handshake state.",
                details={"op": what},
            )
        return self._redis

    def _require_base_url(self) -> str:
        """Return the callback origin or fail closed (unaddressable otherwise).

        Returns:
            The configured public origin.

        Raises:
            InvalidToolError: When `PUBLIC_BASE_URL` is empty (HTTP 400).
        """
        if not self._public_base_url:
            raise InvalidToolError(
                "OAuth login is not configured on this deployment.",
                details={"op": "authorize"},
            )
        return self._public_base_url

    async def build_authorize_url(
        self,
        *,
        tenant_id: str,
        provider: str,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        client_secret: str | None = None,
        scope: str = "",
    ) -> dict[str, Any]:
        """Mint a login URL + handshake state for one provider (spec 0061).

        Args:
            tenant_id: Acting tenant (stored server-side, never in the URL).
            provider: Partner name the credential will land under.
            authorization_endpoint: Provider login page (SSRF-checked).
            token_endpoint: Provider token endpoint (SSRF-checked now, so a
                bad endpoint fails here — not at callback time).
            client_id: OAuth client id (DCR-issued or operator-provisioned).
            client_secret: Confidential-client secret (sealed into state,
                single-use, TTL-bound — never logged).
            scope: Space-separated scopes (passed through untouched).

        Returns:
            `{authorization_url, expires_in}` (no state token — the browser
            carries it inside the provider redirect).

        Raises:
            InvalidToolError: When Redis/base-URL is dark or an endpoint
                fails the safety pre-flight (HTTP 400, identifiers only).
        """
        from voiceai.common.security import is_safe_outbound_url

        redis = self._require_redis("authorize")
        self._require_base_url()
        for label, url in (("authorization_endpoint", authorization_endpoint), ("token_endpoint", token_endpoint)):
            safe = await asyncio.to_thread(is_safe_outbound_url, url)
            if not safe:
                raise InvalidToolError(
                    "OAuth endpoint failed safety validation.",
                    details={"provider": provider, "field": label},
                )
        state = secrets.token_hex(_STATE_BYTES)
        verifier, challenge = _pkce_pair()
        record = {
            "provider": provider,
            "verifier": verifier,
            "client_id": client_id,
            "client_secret": client_secret or "",
            "token_endpoint": token_endpoint,
            "tenant_id": tenant_id,
        }
        await redis.setex(f"{_STATE_KEY_PREFIX}{state}", _STATE_TTL_S, json.dumps(record))
        query = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": _callback_url(self._public_base_url),
            "scope": scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        logger.info("oauth login started for provider")
        return {"authorization_url": f"{authorization_endpoint}?{urlencode(query)}", "expires_in": _STATE_TTL_S}

    async def handle_callback(self, *, code: str, state: str) -> str:
        """Finish a login: verify state, exchange the code, seal tokens.

        Single-use: the state record deletes before the exchange, so a
        replayed callback fails closed even if the first exchange errored.

        Args:
            code: The provider's authorization code (opaque, single-use).
            state: The handshake token round-tripped through the provider.

        Returns:
            The provider name (the popup page carries it, never secrets).

        Raises:
            InvalidToolError: On unknown/expired state, exchange failure, or
                a token response without an access token (HTTP 400).
        """
        redis = self._require_redis("callback")
        raw = await redis.get(f"{_STATE_KEY_PREFIX}{state}")
        if not raw:
            raise InvalidToolError(
                "Login session expired or unknown — restart the login.",
                details={"op": "callback"},
            )
        await redis.delete(f"{_STATE_KEY_PREFIX}{state}")
        try:
            record = json.loads(raw)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            raise InvalidToolError(
                "Login session expired or unknown — restart the login.",
                details={"op": "callback"},
            )
        provider = str(record.get("provider", "provider"))
        tenant_id = str(record.get("tenant_id", ""))
        token_endpoint = str(record.get("token_endpoint", ""))
        if not tenant_id or not token_endpoint or not code:
            raise InvalidToolError(
                "Login session is incomplete — restart the login.",
                details={"provider": provider},
            )
        tokens = await self._exchange(
            token_endpoint=token_endpoint,
            provider=provider,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback_url(self._public_base_url or ""),
                "client_id": str(record.get("client_id", "")),
                "code_verifier": str(record.get("verifier", "")),
            },
            client_id=str(record.get("client_id", "")),
            client_secret=str(record.get("client_secret", "") or ""),
        )
        material: dict[str, Any] = {"token": tokens["access_token"]}
        if tokens.get("refresh_token"):
            material["refresh_token"] = tokens["refresh_token"]
        if record.get("client_secret"):
            material["client_secret"] = record["client_secret"]
        await self._upsert_credential(
            tenant_id=tenant_id,
            provider=provider,
            auth_config={
                "client_id": str(record.get("client_id", "")),
                "token_endpoint": token_endpoint,
            },
            material=material,
        )
        logger.info("oauth login completed for provider")
        return provider

    async def refresh_credential(self, *, tenant_id: str, credential_id: str) -> PartnerCredential:
        """Rotate one oauth2 credential via its stored refresh token.

        Args:
            tenant_id: Acting tenant (scoped read — foreign rows 404).
            credential_id: Storage id.

        Returns:
            The resealed row (secret preserved when the server does not
            rotate it).

        Raises:
            ToolNotFoundError: When no visible row exists (HTTP 404).
            InvalidToolError: When the row is not a refreshable oauth2
                credential or the server refuses (HTTP 400).
        """
        repo = self._repos_for(tenant_id)
        row = await repo.get_credential(credential_id)
        if row is None:
            raise ToolNotFoundError(
                f"Credential {credential_id!r} not found.",
                details={"credential_id": credential_id or ""},
            )
        if row.credential_type != C.CREDENTIAL_TYPE_OAUTH2:
            raise InvalidToolError(
                "Only oauth2 credentials refresh.",
                details={"credential_id": credential_id or ""},
            )
        try:
            material = json.loads(self._vault.decrypt(row.encrypted_data, key_version=row.key_version).decode("utf-8"))
        except Exception as exc:
            raise InvalidToolError(
                "Stored credential cannot be opened for refresh.",
                details={"credential_id": credential_id or ""},
            ) from exc
        if not isinstance(material, dict) or not material.get("refresh_token"):
            raise InvalidToolError(
                "Stored credential has no refresh token — reconnect it.",
                details={"credential_id": credential_id or ""},
            )
        auth_config = row.auth_config if isinstance(row.auth_config, dict) else {}
        token_endpoint = auth_config.get("token_endpoint", "")
        client_id = auth_config.get("client_id", "")
        if not isinstance(token_endpoint, str) or not token_endpoint or not isinstance(client_id, str) or not client_id:
            raise InvalidToolError(
                "Stored credential has no token endpoint — reconnect it.",
                details={"credential_id": credential_id or ""},
            )
        tokens = await self._exchange(
            token_endpoint=token_endpoint,
            provider=row.provider,
            data={
                "grant_type": "refresh_token",
                "refresh_token": str(material["refresh_token"]),
                "client_id": client_id,
            },
            client_id=client_id,
            client_secret=str(material.get("client_secret", "") or ""),
        )
        new_material: dict[str, Any] = {"token": tokens["access_token"]}
        new_material["refresh_token"] = tokens.get("refresh_token") or material["refresh_token"]
        if material.get("client_secret"):
            new_material["client_secret"] = material["client_secret"]
        row.encrypted_data = self._vault.encrypt(json.dumps(new_material).encode("utf-8"), key_version=row.key_version)
        saved = await repo.save_credential(row)
        logger.info("oauth credential refreshed")
        return saved

    async def _upsert_credential(
        self, *, tenant_id: str, provider: str, auth_config: dict[str, Any], material: dict[str, Any]
    ) -> PartnerCredential:
        """Insert or replace one oauth2 row by provider (idempotent login).

        Args:
            tenant_id: Owner tenant (from the state record).
            provider: Partner name (the natural key).
            auth_config: Non-secret placement (`client_id`, `token_endpoint`).
            material: Sealed token JSON (never logged).

        Returns:
            The persisted row.
        """
        repo = self._repos_for(tenant_id)
        existing = [row for row in await repo.find_credentials_by_provider(provider)]
        row = PartnerCredential(
            provider=provider,
            credential_type=C.CREDENTIAL_TYPE_OAUTH2,
            auth_config=dict(auth_config),
            encrypted_data=self._vault.encrypt(json.dumps(material).encode("utf-8"), key_version=1),
            key_version=1,
            expires_at=None,
        )
        if existing and existing[0].id is not None:
            row.id = existing[0].id
        return await repo.save_credential(row)

    async def _exchange(
        self, *, token_endpoint: str, provider: str, data: dict[str, str], client_id: str, client_secret: str
    ) -> dict[str, Any]:
        """POST one token grant and return the decoded token set.

        Args:
            token_endpoint: Provider token URL (SSRF-checked at authorize).
            provider: Partner name (identifiers only, for error details).
            data: Form fields for the grant.
            client_id: OAuth client id.
            client_secret: Confidential-client secret (Basic auth; empty
                means public client — `client_id` rides the body).

        Returns:
            The token mapping (must carry a string `access_token`).

        Raises:
            InvalidToolError: On transport failure, refusal, non-JSON, or a
                token set without an access token (HTTP 400, no token text).
        """
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if client_secret:
            raw = f"{client_id}:{client_secret}".encode()
            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
        try:
            factory = self._http_factory or (
                lambda: httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_S, follow_redirects=False)
            )
            async with factory() as client:
                response = await client.post(token_endpoint, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise InvalidToolError(
                "Token endpoint unreachable during login.",
                details={"provider": provider},
                cause=exc,
            ) from exc
        if response.status_code >= 400:
            raise InvalidToolError(
                "Token endpoint refused the grant.",
                details={"provider": provider},
            )
        try:
            tokens = response.json()
        except ValueError as exc:
            raise InvalidToolError(
                "Token endpoint returned a non-JSON response.",
                details={"provider": provider},
                cause=exc,
            ) from exc
        if (
            not isinstance(tokens, dict)
            or not isinstance(tokens.get("access_token"), str)
            or not tokens["access_token"]
        ):
            raise InvalidToolError(
                "Token endpoint returned no access token.",
                details={"provider": provider},
            )
        return tokens

    def callback_page(self, *, ok: bool, provider: str, detail: str) -> tuple[str, int]:
        """Render the popup page (spec 0061: provider + status, never secrets).

        Args:
            ok: Whether the login completed.
            provider: Partner name (HTML-escaped here).
            detail: Short safe reason.

        Returns:
            `(html, status_code)`.
        """
        status_code = 200 if ok else 400
        return _page(ok, provider, detail, status_code)
