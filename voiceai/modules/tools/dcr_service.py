"""Dynamic Client Registration over provider issuers (spec 0056, Phase-3).

RFC 8414 (Authorization Server Metadata) + RFC 7591 (client registration)
with an OIDC-discovery fallback. Dynamic by design: endpoint paths, timeouts,
and the DCR/MANUAL decision all flow from `constants.py` and the provider's
own metadata — no provider is special-cased here. Callers SSRF-check the
issuer before constructing (this service re-checks off-loop anyway,
fail-closed), and issued secrets go straight to the vault (never logged).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Final

import httpx
from pydantic import BaseModel, Field

from voiceai.common.logger import get_logger
from voiceai.common.security import is_safe_outbound_url
from voiceai.modules.tools import constants as C

__all__ = ["ClientRecord", "DcrService"]

_DCR_PENDING_MESSAGE: Final[str] = "Dynamic Client Registration lands in Phase-3 (spec 0056)"
_WELL_KNOWN_PROTECTED: Final[str] = "/.well-known/oauth-protected-resource"
_WELL_KNOWN_AUTH_SERVER: Final[str] = "/.well-known/oauth-authorization-server"
_WELL_KNOWN_OIDC: Final[str] = "/.well-known/openid-configuration"
_REGISTRATION_KEY: Final[str] = "registration_endpoint"
_AUTHORIZATION_KEY: Final[str] = "authorization_endpoint"
_TOKEN_KEY: Final[str] = "token_endpoint"  # noqa: S105 -- metadata field name, not a secret
_CLIENT_ID_KEY: Final[str] = "client_id"  # noqa: S105 -- metadata field name, not a secret
_CLIENT_SECRET_KEY: Final[str] = "client_secret"  # noqa: S105 -- metadata field name, not a secret
_DCR_TIMEOUT_S: Final[float] = 10.0
_DCR_CLIENT_NAME: Final[str] = "otobaai-tools"


class ClientRecord(BaseModel):
    """An OAuth client registration at one issuer.

    Attributes:
        issuer_url: Provider issuer the client registered with.
        client_id: Issued client id (an identifier — safe to log).
        client_secret: Issued secret, or `None` for public clients (never logged).
    """

    issuer_url: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    client_secret: str | None = None


class DcrService:
    """Discover issuer metadata and register OAuth clients (RFC 8414/7591).

    Args:
        timeout_s: Per-request timeout budget.
        client_name: `client_name` sent at registration (operator-visible only).
        redirect_uris: Callback URLs sent as RFC 7591 `redirect_uris` at
            registration. Issuers that require it (Apollo: "Redirect URI
            can't be blank") refuse the registration without it. `None`
            keeps the old body (client_name + grant_types only) for
            deployments where the public callback URL is unconfigured.
    """

    def __init__(
        self,
        *,
        timeout_s: float = _DCR_TIMEOUT_S,
        client_name: str = _DCR_CLIENT_NAME,
        redirect_uris: Sequence[str] | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._client_name = client_name
        self._redirect_uris = [u for u in (redirect_uris or []) if u]
        self._logger = get_logger(C.MODULE_NAME)

    async def _is_issuer_safe(self, issuer_url: str) -> bool:
        """SSRF-check one issuer URL off the event loop (fail-closed).

        Args:
            issuer_url: Candidate issuer base URL.

        Returns:
            `True` only when the URL is safe to request.
        """
        return await asyncio.to_thread(is_safe_outbound_url, issuer_url)

    async def _fetch_json(self, url: str) -> dict[str, Any] | None:
        """GET one metadata document, returning `None` on any failure.

        Args:
            url: Absolute metadata URL (already SSRF-checked via its issuer).

        Returns:
            The decoded mapping, or `None` when unreachable/non-JSON.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=False) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def discover(self, issuer_url: str) -> dict[str, Any] | None:
        """Discover authorization-server metadata for one issuer.

        Tries the OAuth protected-resource document, then the authorization
        server metadata, then OIDC discovery — first hit wins.

        Args:
            issuer_url: Provider issuer base URL.

        Returns:
            The metadata mapping, or `None` when undiscoverable.
        """
        if not await self._is_issuer_safe(issuer_url):
            self._logger.warning("dcr issuer rejected by ssrf guard")
            return None
        base = issuer_url.rstrip("/")
        for suffix in (_WELL_KNOWN_PROTECTED, _WELL_KNOWN_AUTH_SERVER, _WELL_KNOWN_OIDC):
            metadata = await self._fetch_json(f"{base}{suffix}")
            if metadata and (_AUTHORIZATION_KEY in metadata or _TOKEN_KEY in metadata or _REGISTRATION_KEY in metadata):
                return metadata
        return None

    async def register_client(self, issuer_url: str) -> ClientRecord:
        """Register (or short-circuit) an OAuth client at one issuer.

        Discovery first: issuers without a `registration_endpoint` use MANUAL
        credentials (operator-provisioned), so this raises `ToolExecutionError`
        naming the issuer instead of fabricating a client.

        Args:
            issuer_url: Provider issuer URL (SSRF-checked before any request).

        Returns:
            The issued client record (secret goes to the vault by the caller).

        Raises:
            ToolExecutionError: When discovery fails or the issuer needs MANUAL setup.
        """
        from voiceai.modules.tools.errors import ToolExecutionError

        metadata = await self.discover(issuer_url)
        if metadata is None:
            raise ToolExecutionError(
                "OAuth issuer metadata is undiscoverable.",
                details={"issuer_url": issuer_url},
            )
        registration_endpoint = metadata.get(_REGISTRATION_KEY)
        if not registration_endpoint or not isinstance(registration_endpoint, str):
            raise ToolExecutionError(
                "Issuer requires manual client credentials.",
                details={"issuer_url": issuer_url},
            )
        if not await self._is_issuer_safe(registration_endpoint):
            raise ToolExecutionError(
                "Registration endpoint failed safety validation.",
                details={"issuer_url": issuer_url},
            )
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=False) as client:
                body: dict[str, Any] = {
                    "client_name": self._client_name,
                    "grant_types": ["authorization_code", "refresh_token"],
                }
                if self._redirect_uris:
                    body["redirect_uris"] = list(self._redirect_uris)
                response = await client.post(
                    registration_endpoint,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                "Client registration unreachable.",
                details={"issuer_url": issuer_url},
                cause=exc,
            ) from exc
        if response.status_code >= 400:
            raise ToolExecutionError(
                "Client registration refused.",
                details={"issuer_url": issuer_url},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolExecutionError(
                "Registration endpoint returned a non-JSON response.",
                details={"issuer_url": issuer_url},
                cause=exc,
            ) from exc
        if not isinstance(payload, dict) or _CLIENT_ID_KEY not in payload:
            raise ToolExecutionError(
                "Registration endpoint returned an invalid record.",
                details={"issuer_url": issuer_url},
            )
        client_id = payload[_CLIENT_ID_KEY]
        client_secret = payload.get(_CLIENT_SECRET_KEY)
        if not isinstance(client_id, str) or not client_id:
            raise ToolExecutionError(
                "Registration endpoint returned an invalid record.",
                details={"issuer_url": issuer_url},
            )
        self._logger.info("dcr client registered for issuer")
        return ClientRecord(
            issuer_url=issuer_url,
            client_id=client_id,
            client_secret=client_secret if isinstance(client_secret, str) else None,
        )
