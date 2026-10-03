"""Credential-backed header resolution, cache-first (spec 0056, Phase-1).

`ToolAuthResolver` turns a tool's `auth_ref` pointer into outbound headers.
Redis is a best-effort cache (None-safe; every op bounded by
`AUTH_CACHE_TIMEOUT_S` and fail-open); the durable seam is
`CredentialStorePort`, which Phase-2 wires to the vault/DB rows. Secrets
never reach logs — lines carry the `auth_ref` identifier only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import ToolExecutionError
from voiceai.modules.tools.models import ToolDefinition

__all__ = ["CachePort", "CredentialStorePort", "ToolAuthResolver"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class CachePort(Protocol):
    """TTL cache seam for resolved credential headers (structural, None-safe)."""

    async def get(self, key: str) -> str | None:
        """Return the cached JSON headers, or `None` on a miss.

        Args:
            key: The namespaced cache key.

        Returns:
            The cached JSON, or `None`.
        """
        ...

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Cache JSON headers with a TTL.

        Args:
            key: The namespaced cache key.
            ttl: Seconds the entry stays valid.
            value: The headers JSON.
        """
        ...

    async def delete(self, key: str) -> None:
        """Drop one cached entry (the 401 path calls this before refresh).

        Args:
            key: The namespaced cache key.
        """
        ...


class CredentialStorePort(Protocol):
    """Durable credential seam (Phase-2 wires this to vault/DB rows)."""

    async def fetch_credential(self, auth_ref: str) -> Mapping[str, Any] | None:  # why: vault JSON is open
        """Return the credential envelope for `auth_ref`, or `None` when unknown.

        Args:
            auth_ref: The secret-store pointer carried by the tool row.

        Returns:
            The envelope (`{"token": ...}`, `{"api_key": ...}`, or
            `{"headers": {...}}`), or `None`.
        """
        ...


def _headers_for_credential(credential: Mapping[str, Any], auth_ref: str) -> dict[str, str]:  # why: vault JSON is open
    """Render one credential envelope as outbound headers (pure, no I/O).

    Args:
        credential: The store envelope.
        auth_ref: The pointer (identifiers only — values never leave here).

    Returns:
        The headers to send.

    Raises:
        ToolExecutionError: When the envelope carries no known secret shape.
    """
    token = credential.get(C.AUTH_TOKEN_FIELD)
    if isinstance(token, str) and token:
        return {C.AUTHORIZATION_HEADER: f"{C.AUTH_BEARER_SCHEME} {token}"}
    api_key = credential.get(C.AUTH_API_KEY_FIELD)
    if isinstance(api_key, str) and api_key:
        return {C.AUTH_API_KEY_HEADER: api_key}
    raw_headers = credential.get(C.AUTH_HEADERS_FIELD)
    if isinstance(raw_headers, Mapping):
        headers = {str(key): value for key, value in raw_headers.items() if isinstance(value, str)}
        if headers:
            return headers
    raise ToolExecutionError(
        f"Credential {auth_ref!r} carries no usable secret shape.",
        details={C.AUTH_REF_FIELD: auth_ref},
    )


class ToolAuthResolver:
    """Resolve `auth_ref` pointers into outbound headers, cache-first.

    Args:
        redis_cache: TTL cache (`None` disables caching — resolve straight
            from the store; every op is bounded and fail-open).
        credential_store: Durable seam (`None` until Phase-2 wires vault/DB —
            any `auth_ref` then fails closed, never bare).
        cache_ttl_s: Header-cache TTL.
    """

    def __init__(
        self,
        redis_cache: CachePort | None = None,
        *,
        credential_store: CredentialStorePort | None = None,
        cache_ttl_s: int = C.AUTH_CACHE_TTL_S,
    ) -> None:
        self._cache = redis_cache
        self._store = credential_store
        self._ttl_s = cache_ttl_s

    async def resolve_headers(self, tool: ToolDefinition, *, force_refresh: bool = False) -> dict[str, str]:
        """Return the outbound headers for one tool row.

        Redis first (unless `force_refresh`), then the store with a cache
        write-through. Tools without `auth_ref` answer `{}` and never touch
        either backend.

        Args:
            tool: The row (only `auth_ref` is read).
            force_refresh: Skip the cache read (the 401 path sets this after
                invalidating the stale entry).

        Returns:
            The headers (possibly empty).

        Raises:
            ToolExecutionError: When the ref has no store in this deployment
                or names an unknown credential (fail-closed: never call bare).
        """
        ref = tool.auth_ref
        if ref is None:
            return {}
        if not force_refresh:
            hit = await self._cache_read(ref)
            if hit is not None:
                return hit
        credential = await self._load_credential(ref)
        headers = _headers_for_credential(credential, ref)
        await self._cache_write(ref, headers)
        return headers

    async def invalidate_credential_cache(self, credential_id: str) -> None:
        """Drop one cached header set (the 401 path calls this before refresh).

        Fail-open: a dead cache logs one warning line and the refresh
        proceeds from the store.

        Args:
            credential_id: The `auth_ref` whose headers go stale.
        """
        if self._cache is None:
            return
        try:
            await asyncio.wait_for(
                self._cache.delete(self._cache_key(credential_id)),
                timeout=C.AUTH_CACHE_TIMEOUT_S,
            )
        except Exception as exc:  # cache is best-effort — a dead cache must not fail auth
            logger.warning(C.LOG_AUTH_CACHE_DEGRADED, type(exc).__name__)

    @staticmethod
    def _cache_key(auth_ref: str) -> str:
        """Return the namespaced cache key for one credential pointer.

        Args:
            auth_ref: The secret-store pointer.

        Returns:
            The Redis key.
        """
        return f"{C.AUTH_CACHE_KEY_PREFIX}{auth_ref}"

    async def _cache_read(self, auth_ref: str) -> dict[str, str] | None:
        """Return cached headers, or `None` on miss, corruption, or outage.

        Args:
            auth_ref: The secret-store pointer.

        Returns:
            String-valued headers, or `None` (the caller falls to the store).
        """
        if self._cache is None:
            return None
        try:
            raw = await asyncio.wait_for(
                self._cache.get(self._cache_key(auth_ref)),
                timeout=C.AUTH_CACHE_TIMEOUT_S,
            )
        except Exception as exc:  # cache is best-effort — degrade to the store, never fail
            logger.warning(C.LOG_AUTH_CACHE_DEGRADED, type(exc).__name__)
            return None
        if not isinstance(raw, str) or not raw:
            return None
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(parsed, Mapping):
            return None
        headers = {str(key): value for key, value in parsed.items() if isinstance(value, str)}
        return headers or None

    async def _cache_write(self, auth_ref: str, headers: dict[str, str]) -> None:
        """Write headers through to the cache, swallowing outages with a warning.

        Args:
            auth_ref: The secret-store pointer.
            headers: The resolved headers.
        """
        if self._cache is None:
            return
        try:
            await asyncio.wait_for(
                self._cache.setex(self._cache_key(auth_ref), self._ttl_s, json.dumps(headers)),
                timeout=C.AUTH_CACHE_TIMEOUT_S,
            )
        except Exception as exc:  # cache is best-effort — the headers still return
            logger.warning(C.LOG_AUTH_CACHE_DEGRADED, type(exc).__name__)

    async def _load_credential(self, auth_ref: str) -> Mapping[str, Any]:  # why: vault envelopes are free-form JSON
        """Return the store envelope for one pointer, failing closed.

        Args:
            auth_ref: The secret-store pointer.

        Returns:
            The envelope.

        Raises:
            ToolExecutionError: When no store is wired (Phase-1 without
                vault) or the pointer names nothing.
        """
        if self._store is None:
            raise ToolExecutionError(
                f"Credential {auth_ref!r} has no store in this deployment.",
                details={C.AUTH_REF_FIELD: auth_ref},
            )
        credential = await self._store.fetch_credential(auth_ref)
        if credential is None:
            raise ToolExecutionError(
                f"Credential {auth_ref!r} is unknown.",
                details={C.AUTH_REF_FIELD: auth_ref},
            )
        return credential
