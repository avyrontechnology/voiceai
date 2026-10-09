"""Credential-backed header resolution, cache-first (spec 0056, Phase-1).

`ToolAuthResolver` turns a tool's `auth_ref` pointer into outbound headers.
Redis is a best-effort cache (None-safe; every op bounded by
`AUTH_CACHE_TIMEOUT_S` and fail-open); the durable seam is
`CredentialStorePort`, which Phase-2 wires to the vault/DB rows. Secrets
never reach logs — lines carry the `auth_ref` identifier only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, Field

from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import ToolExecutionError
from voiceai.modules.tools.models import ToolDefinition

__all__ = ["AuthParams", "CachePort", "CredentialStorePort", "ToolAuthResolver"]

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
            auth_ref: The secret-store pointer carried by the tool row
                (`cred:<id>` since spec 0059; anything else fails closed).

        Returns:
            The envelope (`{"credential_type", "auth_config", **material}`
            where material is `{"api_key"}` / `{"token"}` / `{"password"}` /
            `{"headers": {...}}`), or `None`.
        """
        ...


class AuthParams(BaseModel):
    """Resolved outbound auth for one call (spec 0059: headers AND query).

    Query-param keys (`api_key` with `location: query`) cannot ride headers,
    so the resolver returns both halves and the executor merges them into the
    request (query params never carry argument values — only the key).
    """

    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)


def _auth_params_for(
    credential_type: str | None,
    auth_config: Mapping[str, Any],
    material: Mapping[str, Any],
    auth_ref: str,
) -> AuthParams:
    """Render one credential as headers + query params (pure, no I/O).

    Legacy envelopes without `credential_type` keep their Phase-1 shapes
    (`token` → Bearer, `api_key` → `API-KEY` header, `headers` → as-is), so
    header-only behavior is byte-identical to before spec 0059.

    Args:
        credential_type: `none|api_key|bearer|basic|oauth2|custom`, or `None`
            for legacy envelopes.
        auth_config: NON-secret placement (`location`/`name`/`username`).
        material: Sealed secret JSON (never logged, never echoed).
        auth_ref: The pointer (identifiers only — values never leave here).

    Returns:
        The headers + query halves.

    Raises:
        ToolExecutionError: When the envelope carries no usable secret shape.
    """
    if credential_type is None:
        return _legacy_auth_params(material, auth_ref)
    if credential_type == C.CREDENTIAL_TYPE_NONE:
        return AuthParams()
    if credential_type in (C.CREDENTIAL_TYPE_BEARER, C.CREDENTIAL_TYPE_OAUTH2):
        token = material.get(C.AUTH_TOKEN_FIELD)
        if isinstance(token, str) and token:
            return AuthParams(headers={C.AUTHORIZATION_HEADER: f"{C.AUTH_BEARER_SCHEME} {token}"})
    elif credential_type == C.CREDENTIAL_TYPE_API_KEY:
        key = material.get(C.AUTH_API_KEY_FIELD)
        if isinstance(key, str) and key:
            location = auth_config.get(C.AUTH_LOCATION_FIELD, C.AUTH_LOCATION_HEADER)
            name = auth_config.get(C.AUTH_NAME_FIELD, C.DEFAULT_API_KEY_HEADER)
            if not isinstance(name, str) or not name:
                name = C.DEFAULT_API_KEY_HEADER
            if location == C.AUTH_LOCATION_QUERY:
                return AuthParams(query={name: key})
            return AuthParams(headers={name: key})
    elif credential_type == C.CREDENTIAL_TYPE_BASIC:
        username = auth_config.get(C.AUTH_USERNAME_FIELD, "")
        password = material.get(C.AUTH_PASSWORD_FIELD)
        if isinstance(username, str) and isinstance(password, str) and password:
            raw = f"{username}:{password}".encode()
            return AuthParams(headers={C.AUTHORIZATION_HEADER: f"Basic {base64.b64encode(raw).decode('ascii')}"})
    elif credential_type == C.CREDENTIAL_TYPE_CUSTOM:
        raw_headers = material.get(C.AUTH_HEADERS_FIELD)
        if isinstance(raw_headers, Mapping):
            headers = {str(key): value for key, value in raw_headers.items() if isinstance(value, str)}
            if headers:
                return AuthParams(headers=headers)
    raise ToolExecutionError(
        f"Credential {auth_ref!r} carries no usable secret shape.",
        details={C.AUTH_REF_FIELD: auth_ref},
    )


def _legacy_auth_params(material: Mapping[str, Any], auth_ref: str) -> AuthParams:
    """Render a pre-0059 envelope (no `credential_type` key) as before.

    Args:
        material: The store envelope.
        auth_ref: The pointer (identifiers only).

    Returns:
        Headers-only params (query stays empty — legacy parity).

    Raises:
        ToolExecutionError: When the envelope carries no known secret shape.
    """
    token = material.get(C.AUTH_TOKEN_FIELD)
    if isinstance(token, str) and token:
        return AuthParams(headers={C.AUTHORIZATION_HEADER: f"{C.AUTH_BEARER_SCHEME} {token}"})
    api_key = material.get(C.AUTH_API_KEY_FIELD)
    if isinstance(api_key, str) and api_key:
        return AuthParams(headers={C.AUTH_API_KEY_HEADER: api_key})
    raw_headers = material.get(C.AUTH_HEADERS_FIELD)
    if isinstance(raw_headers, Mapping):
        headers = {str(key): value for key, value in raw_headers.items() if isinstance(value, str)}
        if headers:
            return AuthParams(headers=headers)
    raise ToolExecutionError(
        f"Credential {auth_ref!r} carries no usable secret shape.",
        details={C.AUTH_REF_FIELD: auth_ref},
    )


def _headers_for_credential(credential: Mapping[str, Any], auth_ref: str) -> dict[str, str]:  # why: vault JSON is open
    """Render one credential envelope as outbound headers (pure, no I/O).

    Legacy headers-only seam kept for `resolve_headers` compat: query-param
    keys degrade to unusable here (callers needing them use `resolve_auth`).

    Args:
        credential: The store envelope.
        auth_ref: The pointer (identifiers only — values never leave here).

    Returns:
        The headers to send.

    Raises:
        ToolExecutionError: When the envelope carries no known secret shape.
    """
    credential_type = credential.get("credential_type")
    config = credential.get("auth_config", {})
    material = {key: value for key, value in credential.items() if key not in ("credential_type", "auth_config")}
    params = _auth_params_for(
        credential_type if isinstance(credential_type, str) else None,
        config if isinstance(config, Mapping) else {},
        material,
        auth_ref,
    )
    return params.headers


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
        """Return the outbound headers for one tool row (headers-only compat).

        Query-param keys need `resolve_auth` (headers alone cannot carry
        them); this seam stays for callers that only send headers.

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
        return (await self.resolve_auth(tool, force_refresh=force_refresh)).headers

    async def resolve_auth(self, tool: ToolDefinition, *, force_refresh: bool = False) -> AuthParams:
        """Return the outbound headers + query params for one tool row.

        Redis first (unless `force_refresh`), then the store with a cache
        write-through. Tools without `auth_ref` answer empty params and never
        touch either backend.

        Args:
            tool: The row (only `auth_ref` is read).
            force_refresh: Skip the cache read (the 401 path sets this after
                invalidating the stale entry).

        Returns:
            Both auth halves (either may be empty).

        Raises:
            ToolExecutionError: When the ref has no store in this deployment
                or names an unknown credential (fail-closed: never call bare).
        """
        ref = tool.auth_ref
        if ref is None:
            return AuthParams()
        if not force_refresh:
            hit = await self._cache_read(ref)
            if hit is not None:
                return hit
        credential = await self._load_credential(ref)
        credential_type = credential.get("credential_type")
        config = credential.get("auth_config", {})
        material = {key: value for key, value in credential.items() if key not in ("credential_type", "auth_config")}
        params = _auth_params_for(
            credential_type if isinstance(credential_type, str) else None,
            config if isinstance(config, Mapping) else {},
            material,
            ref,
        )
        await self._cache_write(ref, params)
        return params

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

    async def _cache_read(self, auth_ref: str) -> AuthParams | None:
        """Return cached auth halves, or `None` on miss, corruption, or outage.

        Reads both shapes: the spec-0059 `{"headers", "query"}` envelope and
        legacy flat header maps (Phase-1 entries age out by TTL).

        Args:
            auth_ref: The secret-store pointer.

        Returns:
            The cached halves, or `None` (the caller falls to the store).
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
        if isinstance(parsed.get("headers"), Mapping) or isinstance(parsed.get("query"), Mapping):
            headers = parsed.get("headers", {})
            query = parsed.get("query", {})
            params = AuthParams(
                headers={str(key): value for key, value in headers.items() if isinstance(value, str)}
                if isinstance(headers, Mapping)
                else {},
                query={str(key): value for key, value in query.items() if isinstance(value, str)}
                if isinstance(query, Mapping)
                else {},
            )
            return params if params.headers or params.query else None
        headers = {str(key): value for key, value in parsed.items() if isinstance(value, str)}
        return AuthParams(headers=headers) if headers else None

    async def _cache_write(self, auth_ref: str, params: AuthParams) -> None:
        """Write auth halves through to the cache, swallowing outages with a warning.

        Args:
            auth_ref: The secret-store pointer.
            params: The resolved halves.
        """
        if self._cache is None:
            return
        try:
            await asyncio.wait_for(
                self._cache.setex(
                    self._cache_key(auth_ref),
                    self._ttl_s,
                    json.dumps({"headers": params.headers, "query": params.query}),
                ),
                timeout=C.AUTH_CACHE_TIMEOUT_S,
            )
        except Exception as exc:  # cache is best-effort — the halves still return
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
