"""Default JWT mint/verify implementation behind the `TokenService` port (spec 0064, ISP).

`DefaultTokenService` owns the RS256 access-token lifecycle: minting short-lived
access JWTs, verifying bearer tokens back to claims, and persisting the opaque
refresh rows that pair with each access token. The service is stateless beyond
its `JwtSettings` and the store seam it writes refresh rows through — every
method is a pure function of its inputs plus configuration.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import InvalidRequestError
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.service_base import JwtSettings
from voiceai.modules.auth.static_methods import issue_access_token, new_token, token_hash, verify_access_token

__all__ = ["DefaultTokenService"]


class DefaultTokenService:
    """JWT mint/verify implementation satisfying the `TokenService` protocol.

    Args:
        jwt: The JWT configuration, or `None` when the JWT flows stay dark
            (tests, single-proc dev without keys). Every JWT method then
            raises `InvalidRequestError` instead of minting.
        store: The auth persistence behind ``AuthStorePort`` — required for
            `mint_jwt_pair` to persist the opaque refresh row. May be `None`
            when only `verify_jwt` is exercised (offline verification).
    """

    def __init__(self, *, jwt: JwtSettings | None = None, store: AuthStorePort | None = None) -> None:
        self._jwt = jwt
        self._store = store

    async def mint_jwt_pair(self, user: User) -> tuple[str, str]:
        """Mint a fresh access + refresh token pair for a user.

        Args:
            user: The authenticated user (version stamped into the JWT).

        Returns:
            The `(access_token, refresh_token)` pair.
        """
        settings = self._require_jwt()
        moment = utc_now()
        access = issue_access_token(
            user_id=user.user_id,
            org_id=user.org_id,
            role=user.role,
            token_version=self._version_of(user),
            jti=new_token(),
            issuer=settings.issuer,
            audience=settings.audience,
            access_ttl_s=settings.access_ttl_s,
            private_key=settings.private_key,
            now=moment,
        )
        refresh = new_token()
        if self._store is not None:
            await self._store.save_session(
                SessionRecord(
                    token_hash=token_hash(refresh),
                    user_id=user.user_id,
                    org_id=user.org_id,
                    kind="refresh",
                    expires_at=moment + timedelta(seconds=settings.refresh_ttl_s),
                    token_version=user.token_version,
                )
            )
        return access, refresh

    async def verify_jwt(self, secret: str) -> dict[str, Any] | None:
        """Verify a JWT access token and return its claims.

        Args:
            secret: The bearer token from the Authorization header.

        Returns:
            The decoded claims, or `None` when the token is invalid/expired.
        """
        settings = self._require_jwt()
        return verify_access_token(
            secret, public_key=settings.public_key, issuer=settings.issuer, audience=settings.audience
        )

    def _require_jwt(self) -> JwtSettings:
        """Return the JWT configuration, failing loudly when the flows stay dark.

        Raises:
            InvalidRequestError: When no keys are configured — a caller bug or a
                deployment gap, never a store outage.
        """
        if self._jwt is None:
            raise InvalidRequestError("Token pair issuance is not configured")
        return self._jwt

    @staticmethod
    def _version_of(user: User) -> int:
        """Read a row's revocation stamp, defaulting legacy platform rows to zero."""
        version = getattr(user, "token_version", 0)
        return version if isinstance(version, int) else 0
